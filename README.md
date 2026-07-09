# Account Register Manager

This directory is an extracted standalone subset of `yukkcat/chatgpt2api`.
It contains only:

- account pool management APIs
- account import, refresh, update, delete, export
- registration task config/start/stop/reset APIs
- mail provider and registration worker code used by the registration task
- optional upload of newly registered accounts to one or more CLIProxyAPI management endpoints
- a tiny local static admin page

It is not deployed and nothing is started automatically.

## Files

- `account_register_manager/app.py`: FastAPI routes
- `account_register_manager/account_service.py`: local JSON account pool
- `account_register_manager/register_service.py`: registration task runner
- `account_register_manager/register/`: extracted registration worker and mail providers
- `data/accounts.json`: generated account pool storage
- `data/register.json`: generated registration settings

## Local Run

Copy `config.example.json` to `config.json`, change `auth_key`, then run:

```powershell
uvicorn main:app --host 127.0.0.1 --port 8010
```

Open `http://127.0.0.1:8010/` and use the same `auth_key`.

## Docker Run

Copy `config.example.json` to `config.json`, change `auth_key`, then run:

```powershell
docker compose up -d --build
```

Open `http://127.0.0.1:8010/`. The compose file mounts:

- `./config.json` to persist settings
- `./data` to persist account pool and registration settings

You can also override the login key with an environment variable:

```powershell
$env:ACCOUNT_REGISTER_AUTH_KEY = "your-auth-key"
docker compose up -d --build
```

Useful Docker commands:

```powershell
docker compose logs -f
docker compose restart
docker compose down
```

## Notes

The registration settings are still stored in `data/register.json`. You must configure
at least one enabled mail provider before starting registration.

If you configure upstream CLIProxyAPI upload targets in Settings, every newly
registered account is uploaded to each enabled target with:

```text
POST /v0/management/auth-files?name=<account>.json
Authorization: Bearer <management-key>
Content-Type: application/json
```

No upload is attempted when the target list is empty.

## Sync Registration Sources

When the upstream project changes registration code, run this from the repository root:

```powershell
python .\extracted\account-register-manager\scripts\sync_register_sources.py
```

The script copies `services/register/openai_register.py` and
`services/register/mail_provider.py`, then reapplies the standalone import/path
adaptations.

This repository also includes a GitHub Actions workflow that can sync those
files automatically from `yukkcat/chatgpt2api` on a schedule or by manual
dispatch.
