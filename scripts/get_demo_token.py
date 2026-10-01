"""Sign a demo user in and print ONLY the access token (paste it into Swagger's Authorize).

Reads DEMO_USER_EMAIL / DEMO_USER_PASSWORD (or DEMO_ADMIN_* with --admin) and
SUPABASE_PUBLISHABLE_KEY from ~/.secrets/booking-agent.env (override with --env-file).
SUPABASE_URL comes from that file or the environment, else the hosted demo project.
Nothing but the token goes to stdout; the password and refresh token are never printed.
Use --length-only to check that sign-in works without printing the token.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ENV_FILE = Path("~/.secrets/booking-agent.env").expanduser()
DEFAULT_SUPABASE_URL = "https://dhovoboznckrknphwpir.supabase.co"
# Cloudflare (in front of the host) blocks Python's default User-Agent with a 403.
USER_AGENT = "Mozilla/5.0 (compatible; booking-agent-demo-token/1.0)"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def fetch_token(base_url: str, publishable_key: str, email: str, password: str) -> str:
    url = f"{base_url.rstrip('/')}/auth/v1/token?grant_type=password"
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise SystemExit("SUPABASE_URL must be https (or a local address)")
    request = urllib.request.Request(  # noqa: S310 (scheme checked above)
        url,
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={
            "apikey": publishable_key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        # Report the status only: the response body could echo request details.
        raise SystemExit(f"sign-in failed: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise SystemExit("sign-in failed: could not reach Supabase") from None
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise SystemExit("sign-in failed: no access token in the response")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--admin", action="store_true", help="sign in the demo admin instead")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--length-only", action="store_true", help="print only the token length")
    args = parser.parse_args()

    if not args.env_file.is_file():
        raise SystemExit(f"env file not found: {args.env_file}")
    env = read_env_file(args.env_file)
    prefix = "DEMO_ADMIN" if args.admin else "DEMO_USER"
    needed = ["SUPABASE_PUBLISHABLE_KEY", f"{prefix}_EMAIL", f"{prefix}_PASSWORD"]
    missing = [name for name in needed if not env.get(name)]
    if missing:
        raise SystemExit(f"missing in env file: {', '.join(missing)}")

    base_url = os.environ.get("SUPABASE_URL") or env.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL
    token = fetch_token(
        base_url, env["SUPABASE_PUBLISHABLE_KEY"], env[f"{prefix}_EMAIL"], env[f"{prefix}_PASSWORD"]
    )
    sys.stdout.write(f"token length: {len(token)}\n" if args.length_only else f"{token}\n")


if __name__ == "__main__":
    main()
