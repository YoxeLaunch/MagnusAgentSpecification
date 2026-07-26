"""Resolución de perfiles y selección de proveedor con fallback.

Los agentes MAS declaran `model.profile`; aquí se resuelve contra models.yaml a
un (provider, model, params) concreto. Este módulo es el ÚNICO lugar donde vive
la política de failover: ni el motor ni los adaptadores deciden a quién llamar
cuando algo falla.

Política de fallback (paso 2.3 del ROADMAP)
-------------------------------------------
Ante un fallo se intentan, en este orden:

  1. `primary` del perfil pedido (con reintentos acotados si el error es
     reintentable).
  2. `fallback` interno del perfil (`models.yaml`) — es el sustituto más
     cercano: mismo propósito, otro proveedor.
  3. `fallback_profile` del agente (`agent.yaml`), si el llamante lo pasa — es
     un cambio de perfil, es decir una degradación deliberada de capacidad, y
     por eso va después del sustituto equivalente.

Qué errores autorizan failover:

  - SÍ: indisponibilidad (no hay adaptador para ese proveedor), timeout,
    rate limit, 5xx — todo lo que el adaptador marque `retryable=True`.
  - NO: credenciales inválidas (401/403), petición inválida (400), rechazo del
    modelo. Cambiar de proveedor ante un 401 no arregla nada: oculta un error
    de configuración detrás de una factura en otro proveedor.

Un perfil inexistente o un proveedor sin adaptador fallan con un error
normalizado y explícito, nunca con un `KeyError` crudo.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .base import LLMProvider, LLMRequest, LLMResponse, ProviderError, ResolvedModel


class ProfileNotFound(ProviderError):
    """El perfil pedido no existe en models.yaml."""


class ProviderUnavailable(ProviderError):
    """El perfil resuelve a un proveedor para el que no hay adaptador."""

    def __init__(self, message: str):
        super().__init__(message, retryable=True)  # reintentable = puede caer al fallback


@dataclass
class ProfileConfig:
    primary: ResolvedModel
    fallback: ResolvedModel | None = None


@dataclass(frozen=True)
class ProviderAttempt:
    """Un intento concreto contra un (proveedor, modelo). Trazable."""
    profile: str
    provider: str
    model: str
    kind: str                      # primary | profile_fallback | agent_fallback
    latency_ms: float
    ok: bool
    error: str | None = None
    retryable: bool | None = None

    def as_dict(self) -> dict:
        d = {"perfil": self.profile, "proveedor": self.provider, "modelo": self.model,
             "tipo": self.kind, "latencia_ms": round(self.latency_ms, 1), "ok": self.ok}
        if self.error is not None:
            d["error"] = self.error
            d["reintentable"] = self.retryable
        return d


@dataclass(frozen=True)
class ProviderTrace:
    """Qué se pidió, qué respondió y si hubo fallback — por consulta."""
    requested_profile: str
    attempts: list[ProviderAttempt] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].ok

    @property
    def final(self) -> ProviderAttempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def used_fallback(self) -> bool:
        return self.succeeded and self.attempts[-1].kind != "primary"

    @property
    def total_latency_ms(self) -> float:
        return sum(a.latency_ms for a in self.attempts)

    def as_dict(self) -> dict:
        return {
            "perfil_solicitado": self.requested_profile,
            "proveedor_final": self.final.provider if self.succeeded else None,
            "modelo_final": self.final.model if self.succeeded else None,
            "fallback_aplicado": self.used_fallback,
            "latencia_total_ms": round(self.total_latency_ms, 1),
            "intentos": [a.as_dict() for a in self.attempts],
        }


class ProviderRegistry:
    def __init__(self, providers: dict[str, LLMProvider], profiles: dict[str, ProfileConfig],
                 *, retries: int = 1, backoff_s: float = 0.5, sleep=time.sleep,
                 local_providers: set[str] | None = None):
        self._providers = providers          # name -> adapter
        self._profiles = profiles            # profile name -> ProfileConfig
        self._retries = max(0, retries)      # reintentos ADICIONALES por endpoint
        self._backoff_s = backoff_s
        self._sleep = sleep
        # Qué adaptadores no sacan datos del dispositivo. Lo usa la política de
        # egreso (orchestration/privacy.py) para acotar el plan de fallback.
        self._local = local_providers if local_providers is not None else {"ollama"}

    @classmethod
    def from_yaml(cls, path: str, providers: dict[str, LLMProvider], **kwargs) -> "ProviderRegistry":
        import yaml
        raw = yaml.safe_load(open(path, encoding="utf-8"))

        def _rm(d: dict) -> ResolvedModel:
            return ResolvedModel(
                provider=d["provider"], model=d["model"],
                params={k: v for k, v in d.items() if k not in ("provider", "model")},
            )

        profiles: dict[str, ProfileConfig] = {}
        for name, cfg in raw["profiles"].items():
            profiles[name] = ProfileConfig(
                primary=_rm(cfg["primary"]),
                fallback=_rm(cfg["fallback"]) if "fallback" in cfg else None,
            )
        return cls(providers, profiles, **kwargs)

    # -- introspección -------------------------------------------------------
    def profiles(self) -> list[str]:
        return sorted(self._profiles)

    def adapters(self) -> list[str]:
        return sorted(self._providers)

    def is_available(self, profile: str, *, only_local: bool = False) -> bool:
        """¿Hay algún endpoint utilizable (primario o fallback) para el perfil?"""
        cfg = self._profiles.get(profile)
        if cfg is None:
            return False
        return any(rm is not None and rm.provider in self._providers
                   and (not only_local or rm.provider in self._local)
                   for rm in (cfg.primary, cfg.fallback))

    def is_local(self, provider: str) -> bool:
        return provider in self._local

    def resolve(self, profile: str) -> ProfileConfig:
        if profile not in self._profiles:
            raise ProfileNotFound(
                f"perfil de modelo desconocido: '{profile}'. "
                f"Perfiles declarados en models.yaml: {self.profiles()}")
        return self._profiles[profile]

    # -- ejecución -----------------------------------------------------------
    def complete(self, req: LLMRequest, *, fallback_profile: str | None = None,
                 only_local: bool = False) -> LLMResponse:
        resp, _ = self.complete_with_trace(req, fallback_profile=fallback_profile,
                                           only_local=only_local)
        return resp

    def complete_with_trace(
        self, req: LLMRequest, *, fallback_profile: str | None = None,
        only_local: bool = False,
    ) -> tuple[LLMResponse, ProviderTrace]:
        """Ejecuta aplicando la política de fallback y devuelve la traza.

        `only_local=True` poda del plan cualquier proveedor remoto. Lo usa el
        motor cuando la política de egreso prohíbe que los namespaces
        recuperados salgan del dispositivo: el fallback automático no puede
        convertirse en la puerta trasera por la que salen igualmente.
        """
        trace = ProviderTrace(req.profile, [])
        plan = self._plan(req.profile, fallback_profile)
        if only_local:
            plan = [p for p in plan if p[1].provider in self._local]
            if not plan:
                raise ProviderUnavailable(
                    f"el perfil '{req.profile}' no tiene ningún endpoint local y la "
                    f"política de privacidad prohíbe el egreso remoto. "
                    f"Adaptadores locales: {sorted(self._local & set(self._providers)) or '(ninguno)'}")

        ultimo: ProviderError | None = None
        for profile, resolved, kind in plan:
            intento_req = req if profile == req.profile else _con_perfil(req, profile)
            resp, error = self._intentar(intento_req, profile, resolved, kind, trace)
            if error is None:
                return resp, trace
            if not error.retryable:
                # 401/400: fallar claro aquí mismo, sin probar otro proveedor.
                raise error
            ultimo = error

        raise ultimo or ProviderUnavailable(
            f"ningún endpoint utilizable para el perfil '{req.profile}'")

    # -- interno -------------------------------------------------------------
    def _plan(self, profile: str, fallback_profile: str | None
              ) -> list[tuple[str, ResolvedModel, str]]:
        """Orden de intentos: primario → fallback del perfil → perfil del agente."""
        cfg = self.resolve(profile)
        plan: list[tuple[str, ResolvedModel, str]] = [(profile, cfg.primary, "primary")]
        if cfg.fallback is not None:
            plan.append((profile, cfg.fallback, "profile_fallback"))
        if fallback_profile and fallback_profile != profile:
            alt = self.resolve(fallback_profile)
            plan.append((fallback_profile, alt.primary, "agent_fallback"))
            if alt.fallback is not None:
                plan.append((fallback_profile, alt.fallback, "agent_fallback"))
        return plan

    def _intentar(self, req: LLMRequest, profile: str, resolved: ResolvedModel,
                  kind: str, trace: ProviderTrace) -> tuple[LLMResponse | None, ProviderError | None]:
        adapter = self._providers.get(resolved.provider)
        if adapter is None:
            error = ProviderUnavailable(
                f"el perfil '{profile}' resuelve a provider '{resolved.provider}', "
                f"para el que no hay adaptador registrado. "
                f"Adaptadores disponibles: {self.adapters() or '(ninguno)'}")
            trace.attempts.append(ProviderAttempt(
                profile, resolved.provider, resolved.model, kind, 0.0,
                ok=False, error=str(error), retryable=True))
            return None, error

        for intento in range(self._retries + 1):
            t0 = time.perf_counter()
            try:
                resp = adapter.complete(req, resolved)
            except ProviderError as e:
                dt = (time.perf_counter() - t0) * 1000
                trace.attempts.append(ProviderAttempt(
                    profile, resolved.provider, resolved.model, kind, dt,
                    ok=False, error=str(e), retryable=e.retryable))
                if not e.retryable or intento == self._retries:
                    return None, e
                self._sleep(self._backoff_s * (2 ** intento))
            except Exception as e:  # adaptador que no normalizó su error
                dt = (time.perf_counter() - t0) * 1000
                err = ProviderError(
                    f"error no normalizado del adaptador '{resolved.provider}': {e}",
                    retryable=False)
                trace.attempts.append(ProviderAttempt(
                    profile, resolved.provider, resolved.model, kind, dt,
                    ok=False, error=str(err), retryable=False))
                return None, err
            else:
                dt = (time.perf_counter() - t0) * 1000
                trace.attempts.append(ProviderAttempt(
                    profile, resolved.provider, resp.model or resolved.model, kind, dt, ok=True))
                return resp, None
        return None, ProviderError("estado inalcanzable en el bucle de reintentos")


def _con_perfil(req: LLMRequest, profile: str) -> LLMRequest:
    from dataclasses import replace
    return replace(req, profile=profile)
