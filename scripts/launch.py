import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from dotenv import load_dotenv, dotenv_values

ROOT = Path(__file__).resolve().parent.parent


def command(service):
    python = sys.executable
    if service == "gateway":
        return [python, "-m", "uvicorn", "main:app", "--app-dir", str(ROOT / "gateway"),
                "--host", os.environ["GATEWAY_BIND_HOST"], "--port", os.environ["GATEWAY_PORT"]]
    if service == "ai":
        model = ROOT / os.environ["AI_MODEL_PATH"]
        return [python, str(ROOT / "model/run_realtime.py"),
                "--gateway", os.environ["GATEWAY_URL"], "--session", os.environ["SESSION_ID"],
                "--model", str(model)]
    return [os.environ["MOSQUITTO_EXE"]]


def run(service):
    if not (ROOT / ".env").is_file():
        raise ValueError("Missing project .env; copy .env.example to .env first")
    load_dotenv(ROOT / ".env", override=True)
    if "DATABASE_PATH" not in dotenv_values(ROOT / ".env"):
        os.environ.pop("DATABASE_PATH", None)
    args = command(service)
    if service != "mosquitto":
        return subprocess.call(args, cwd=ROOT)
    port = int(os.environ["MQTT_PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("MQTT_PORT must be between 1 and 65535")
    template = (ROOT / "scripts/mosquitto.conf").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="rf-mosquitto-") as folder:
        config = Path(folder) / "mosquitto.conf"
        config.write_text(template.replace("${MQTT_PORT}", str(port)), encoding="ascii")
        return subprocess.call(args + ["-c", str(config), "-v"], cwd=ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("mosquitto", "gateway", "ai"))
    try:
        sys.exit(run(parser.parse_args().service))
    except KeyboardInterrupt:
        sys.exit(0)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"Cannot start service: {error}\n")
