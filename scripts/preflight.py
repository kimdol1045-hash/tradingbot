"""
Preflight Check — validate environment before starting the trading bot.
Run: python scripts/preflight.py

Checks:
  1. Python version ≥ 3.12
  2. Required env vars present
  3. DB directory writable
  4. Hyperliquid API connectivity
  5. Telegram Bot API reachable
  6. OpenAI API key valid
  7. Param files exist for all agents
  8. Disk space
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"


def check(name: str, ok: bool, detail: str = "") -> bool:
    icon = PASS if ok else FAIL
    msg = f"  {icon} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return ok


def main() -> int:
    print("═══ Tradingbot Preflight Check ═══\n")
    results: list[bool] = []

    # 1. Python version
    v = sys.version_info
    results.append(check(
        "Python version",
        v >= (3, 12),
        f"{v.major}.{v.minor}.{v.micro}",
    ))

    # 2. Load .env
    from dotenv import load_dotenv
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"  {PASS} .env file loaded")
    else:
        print(f"  {WARN} .env file not found (using environment vars)")

    # 3. Required env vars
    use_openclaw = os.getenv("USE_OPENCLAW", "false").lower() == "true"

    required_vars = {
        "HYPERLIQUID_KEY": os.getenv("HYPERLIQUID_KEY", ""),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
    }
    if use_openclaw:
        required_vars["OPENCLAW_GATEWAY_TOKEN"] = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
    else:
        required_vars["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

    print("\n── Environment Variables ──")
    for var, val in required_vars.items():
        is_set = bool(val) and val not in ("your_api_key_here", "your_api_secret_here",
                                            "your_telegram_bot_token", "your_chat_id",
                                            "your_openai_key")
        results.append(check(var, is_set, "set" if is_set else "MISSING or placeholder"))

    # 4. DB directory
    print("\n── Storage ──")
    db_path = Path(os.getenv("DB_PATH", "data/trades.db"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    db_dir = db_path.parent
    db_dir.mkdir(parents=True, exist_ok=True)
    writable = os.access(db_dir, os.W_OK)
    results.append(check("DB directory writable", writable, str(db_dir)))

    # 5. Disk space
    disk = shutil.disk_usage(str(PROJECT_ROOT))
    free_gb = disk.free / (1024 ** 3)
    results.append(check("Disk space", free_gb > 1.0, f"{free_gb:.1f} GB free"))

    # 6. Param files
    print("\n── Agent Parameters ──")
    for agent_id in ["s1", "s2", "s3", "s4"]:
        param_file = PROJECT_ROOT / "params" / agent_id / "params.json"
        results.append(check(f"params/{agent_id}/params.json", param_file.exists()))

    # 7. Multi-wallet check
    print("\n── Wallet Configuration ──")
    global_key = os.getenv("HYPERLIQUID_KEY", "")
    per_agent_keys = {}
    for agent_id in ["s1", "s2", "s3", "s4"]:
        env_var = f"HYPERLIQUID_KEY_{agent_id.upper()}"
        val = os.getenv(env_var, "")
        if val:
            per_agent_keys[agent_id] = val

    if per_agent_keys:
        print(f"  {PASS} Multi-wallet mode: {len(per_agent_keys)} per-agent keys found")
        # Validate each key
        try:
            import eth_account
            addresses = []
            for agent_id, key in per_agent_keys.items():
                try:
                    wallet = eth_account.Account.from_key(key)
                    addresses.append(wallet.address)
                    results.append(check(
                        f"HYPERLIQUID_KEY_{agent_id.upper()}", True,
                        f"valid ({wallet.address[:10]}...)",
                    ))
                except Exception as e:
                    results.append(check(
                        f"HYPERLIQUID_KEY_{agent_id.upper()}", False,
                        f"invalid key: {str(e)[:40]}",
                    ))
            # Warn if all keys are the same
            unique_addresses = set(addresses)
            if len(unique_addresses) == 1 and len(addresses) > 1:
                print(f"  {WARN} All per-agent keys resolve to the same address — no isolation benefit")
        except ImportError:
            print(f"  {WARN} eth_account not installed — cannot validate wallet keys")
    elif global_key and global_key != "your_api_key_here":
        print(f"  {PASS} Legacy single-wallet mode (HYPERLIQUID_KEY)")
    else:
        print(f"  {WARN} No wallet keys configured")

    # 8. API connectivity (optional — requires network)
    print("\n── API Connectivity ──")

    try:
        import httpx

        # Hyperliquid info endpoint
        try:
            resp = httpx.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "meta"},
                timeout=10,
            )
            results.append(check("Hyperliquid API", resp.status_code == 200, f"HTTP {resp.status_code}"))
        except Exception as e:
            results.append(check("Hyperliquid API", False, str(e)[:60]))

        # Telegram
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if token and token != "your_telegram_bot_token":
            try:
                resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
                ok = resp.status_code == 200 and resp.json().get("ok")
                results.append(check("Telegram Bot API", ok, "connected" if ok else f"HTTP {resp.status_code}"))
            except Exception as e:
                results.append(check("Telegram Bot API", False, str(e)[:60]))
        else:
            print(f"  {WARN} Telegram — skipped (no token)")

        # OpenClaw or OpenAI
        if use_openclaw:
            gw_url = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
            gw_token = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
            if gw_token:
                try:
                    resp = httpx.post(
                        f"{gw_url}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {gw_token}", "Content-Type": "application/json"},
                        json={"model": "openclaw", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
                        timeout=15,
                    )
                    ok = resp.status_code == 200
                    results.append(check("OpenClaw Gateway", ok, "connected" if ok else f"HTTP {resp.status_code}"))
                except Exception as e:
                    results.append(check("OpenClaw Gateway", False, str(e)[:60]))
            else:
                print(f"  {WARN} OpenClaw — skipped (no token)")
        else:
            oai_key = os.getenv("OPENAI_API_KEY", "")
            if oai_key and oai_key != "your_openai_key":
                try:
                    resp = httpx.get(
                        "https://api.openai.com/v1/models",
                        headers={"Authorization": f"Bearer {oai_key}"},
                        timeout=10,
                    )
                    ok = resp.status_code == 200
                    results.append(check("OpenAI API", ok, "connected" if ok else f"HTTP {resp.status_code}"))
                except Exception as e:
                    results.append(check("OpenAI API", False, str(e)[:60]))
            else:
                print(f"  {WARN} OpenAI — skipped (no key)")

    except ImportError:
        print(f"  {WARN} httpx not installed — skipping API checks")

    # 9. Wallet Addresses (balance monitoring)
    print("\n── Wallet Address Monitoring ──")
    wallet_addrs = {}
    for agent_id in ["s1", "s2", "s3", "s4"]:
        addr = os.getenv(f"WALLET_ADDRESS_{agent_id.upper()}", "")
        if addr:
            wallet_addrs[agent_id] = addr
    if wallet_addrs:
        print(f"  {PASS} Balance monitoring: {len(wallet_addrs)} addresses configured")
        for aid, addr in wallet_addrs.items():
            print(f"    {aid}: {addr[:12]}...{addr[-6:]}")
    else:
        print(f"  {WARN} No WALLET_ADDRESS_Sx vars set — balance monitoring disabled")

    # 10. Dashboard dependencies
    print("\n── Dashboard ──")
    try:
        import fastapi
        results.append(check("FastAPI", True, f"v{fastapi.__version__}"))
    except ImportError:
        results.append(check("FastAPI", False, "pip install fastapi"))
    try:
        import uvicorn
        results.append(check("Uvicorn", True, f"v{uvicorn.__version__}"))
    except ImportError:
        results.append(check("Uvicorn", False, "pip install uvicorn[standard]"))

    dashboard_port = int(os.getenv("DASHBOARD_PORT", "8501"))
    print(f"  Dashboard port: {dashboard_port}")

    # 11. symbols.json
    print("\n── Symbol Pool ──")
    symbols_path = PROJECT_ROOT / "symbols.json"
    if symbols_path.exists():
        try:
            import json
            with open(symbols_path) as f:
                sdata = json.load(f)
            pool = sdata.get("symbol_pool", {})
            total_coins = sum(len(v) for v in pool.values())
            results.append(check("symbols.json", total_coins > 0,
                                 f"{total_coins} coins across {len(pool)} tiers"))
        except Exception as e:
            results.append(check("symbols.json", False, f"parse error: {str(e)[:40]}"))
    else:
        print(f"  {WARN} symbols.json not found — using default symbol pool")

    # 12. Module imports
    print("\n── Module Imports ──")
    critical_modules = [
        "src.utils.config",
        "src.utils.db",
        "src.pipeline.phase1_safety",
        "src.pipeline.phase2_read",
        "src.pipeline.phase3_scan",
        "src.pipeline.phase4_gate",
        "src.pipeline.phase5_execute",
        "src.pipeline.runner",
        "src.pipeline.position_manager",
        "src.pipeline.equity_tracker",
        "src.collector.collector",
        "src.openclaw.evolver",
        "src.notify.telegram",
        "src.dashboard.app",
    ]
    import importlib
    for mod in critical_modules:
        try:
            importlib.import_module(mod)
            results.append(check(mod, True))
        except Exception as e:
            results.append(check(mod, False, str(e)[:80]))

    # Summary
    passed = sum(results)
    total = len(results)
    failed = total - passed
    print(f"\n═══ Result: {passed}/{total} passed", end="")
    if failed:
        print(f", {failed} failed ═══")
        return 1
    else:
        print(" ═══")
        print("\nAll checks passed. Ready to start.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
