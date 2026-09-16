"""Test data management (spec §17) — Phase 14.

- Generation: faker-style synthesis without the faker dependency (user, email,
  string, uuid, int) — deterministic per-execution via the `seed` param.
- Resolution: merge environment variables + active datasets into the flat
  `variables` dict the executors already accept. Secret values are decrypted
  only here, just before execution — never logged, never returned by the API.
- Storage: secret values are Fernet-encrypted at rest (keys listed in
  `encrypted_keys`).
"""
import random
import string
import uuid as uuid_mod

from app.security.crypto import decrypt_json, encrypt_json, mask_secrets

_SECRET_KEY_HINTS = ("password", "secret", "token", "api_key", "apikey", "authorization", "cookie", "credential")


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(h in lowered for h in _SECRET_KEY_HINTS)


# ------------------------------------------------------------ generators


def generate_value(generator: str, params: dict, seed: int | None = None) -> object:
    """Synthesize one value. Deterministic when `seed` is given (reproducible
    runs); random otherwise."""
    rng = random.Random(seed) if seed is not None else random.Random()
    gen = (generator or "string").lower()

    if gen == "uuid":
        base = uuid_mod.UUID(int=rng.getrandbits(128), version=4) if seed is not None else uuid_mod.uuid4()
        return str(base)
    if gen == "int":
        lo = int(params.get("min", 1))
        hi = int(params.get("max", 10000))
        return rng.randint(lo, hi)
    if gen == "email":
        return generate_value("user", params, seed).get("email")
    if gen == "user":
        first = rng.choice(["alex", "sam", "jordan", "taylor", "casey", "riley", "morgan", "quinn"])
        last = rng.choice(["lee", "park", "garcia", "nguyen", "khan", "silva", "okafor", "weber"])
        domain = str(params.get("domain", "example.com"))
        digits = str(rng.randint(10, 9999))
        return {
            "username": f"{first}.{last}{digits}",
            "email": f"{first}.{last}{digits}@{domain}",
            "first_name": first.capitalize(),
            "last_name": last.capitalize(),
            "password": "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(14)) + "!7",
        }
    # default: string
    length = int(params.get("length", 12))
    prefix = str(params.get("prefix", ""))
    chars = string.ascii_letters + string.digits
    return prefix + "".join(rng.choice(chars) for _ in range(length))


def generate_dataset_values(dataset, seed: int | None = None) -> dict:
    """Expand a generated dataset into concrete values. Value keys come from
    the dataset's `values` mapping (e.g. {"signup_email": "x"} → the generated
    value lands in `signup_email`); when values is empty, the dataset name is
    used as the single key."""
    params = dataset.generator_params or {}
    if dataset.generator == "user":
        return generate_value("user", params, seed)
    base = generate_value(dataset.generator or "string", params, seed)
    keys = [k for k in (dataset.values or {}) if k] or [dataset.name]
    return {str(key): base for key in keys}


# ------------------------------------------------------------ storage


def encrypt_dataset_values(values: dict) -> tuple[dict, list[str]]:
    """Split values into plaintext + encrypted; returns (stored, encrypted_keys)."""
    plain: dict = {}
    secrets: dict = {}
    for key, value in (values or {}).items():
        if is_secret_key(str(key)) and value:
            secrets[str(key)] = value
        else:
            plain[str(key)] = value
    if secrets:
        return {**plain, "__enc__": encrypt_json(secrets)}, list(secrets.keys())
    return plain, []


def decrypt_dataset_values(stored: dict, encrypted_keys: list | None = None) -> dict:
    """Full plaintext view (server-side only — never serialize to the API)."""
    values = {k: v for k, v in (stored or {}).items() if k != "__enc__"}
    enc_blob = (stored or {}).get("__enc__")
    if enc_blob:
        values.update(decrypt_json(enc_blob))
    return values


def mask_dataset_values(stored: dict) -> dict:
    """API-safe view: decrypt the sealed blob, then mask secret values."""
    return mask_secrets(decrypt_dataset_values(stored))


# ------------------------------------------------------------ resolution


def resolve_variables(
    project,
    environment_id: str | None = None,
    seed: int | None = None,
    db=None,
) -> dict:
    """Flat variables dict for executors: project credentials (encrypted blob)
    + environment variables + active static datasets + generated datasets.
    Later layers override earlier ones (dataset > environment > project).

    `db` is optional for callers with an open session (API request); a fresh
    SessionLocal is used otherwise (workers).
    """
    import sqlalchemy as sa

    from app.db import SessionLocal
    from app.models import TestDataset, TestEnvironment

    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        variables: dict = decrypt_json(project.credentials_encrypted or "")

        env_row = None
        if environment_id:
            env_row = db.execute(
                sa.select(TestEnvironment).where(
                    TestEnvironment.id == environment_id,
                    TestEnvironment.project_id == project.id,
                )
            ).scalar_one_or_none()
        elif environment_id is None:
            # No environment requested → do NOT silently apply one. Only a
            # project with exactly one environment gets it as the default;
            # otherwise env vars would leak across targets.
            count = db.execute(
                sa.select(sa.func.count())
                .select_from(TestEnvironment)
                .where(TestEnvironment.project_id == project.id)
            ).scalar_one()
            if count == 1:
                env_row = db.execute(
                    sa.select(TestEnvironment).where(TestEnvironment.project_id == project.id)
                ).scalar_one()
        if env_row:
            variables.update(env_row.variables or {})
            if env_row.base_url:
                variables.setdefault("base_url", env_row.base_url)

        datasets = db.execute(
            sa.select(TestDataset)
            .where(TestDataset.project_id == project.id, TestDataset.is_active.is_(True))
            .order_by(TestDataset.created_at)
        ).scalars().all()
        active_env_id = str(env_row.id) if env_row else None
        # Datasets scoped to a specific environment only apply to that env;
        # global datasets (environment_id null) always apply.
        for ds in datasets:
            if ds.environment_id and str(ds.environment_id) != active_env_id:
                continue
            if ds.kind == "generated":
                resolved = generate_dataset_values(ds, seed)
            else:
                resolved = decrypt_dataset_values(ds.values, ds.encrypted_keys or [])
            variables.update(resolved)
        return variables
    finally:
        if own_session:
            db.close()
