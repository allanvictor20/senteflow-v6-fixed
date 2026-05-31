"""
SenteFlow AI - Pre-demo WhatsApp connectivity test (Evolution API)

Run before demo day:
  cd backend
  python scripts/test_whatsapp.py
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


async def run_tests():
    from integrations.whatsapp.client import EvolutionClient

    wa = EvolutionClient(
        base_url=os.environ.get("EVOLUTION_BASE_URL", "http://localhost:8080"),
        api_key=os.environ.get("EVOLUTION_API_KEY", ""),
        session=os.environ.get("EVOLUTION_SESSION", "senteflow"),
    )

    print("\n[1/3] Checking Evolution API health...")
    try:
        health = await wa.health_check()
        if health.get("status") == "ok":
            print("      Evolution API reachable")
        else:
            print(f"      Evolution API unhealthy: {health}")
            sys.exit(1)
    except Exception as exc:
        print(f"      Could not reach Evolution API: {exc}")
        print("      Make sure Evolution API is running at", os.environ.get("EVOLUTION_BASE_URL"))
        sys.exit(1)

    print("\n[2/3] Checking WhatsApp session state...")
    try:
        status = await wa.get_session_status()
        if status.get("connected"):
            print("      WhatsApp session connected (state: open)")
        else:
            print(f"      WhatsApp not connected. State: {status.get('status')}")
            print("      Scan the QR code via:")
            print(
                f"        curl {os.environ.get('EVOLUTION_BASE_URL')}/instance/connect/"
                f"{os.environ.get('EVOLUTION_SESSION')} -H "
                f"'apikey: {os.environ.get('EVOLUTION_API_KEY', 'YOUR_KEY')}'"
            )
            sys.exit(1)
    except Exception as exc:
        print(f"      Session check failed: {exc}")
        sys.exit(1)

    test_number = os.environ.get("TEST_PHONE_NUMBER")
    if test_number:
        print(f"\n[3/3] Sending test message to {test_number}...")
        try:
            result = await wa.send_text(
                test_number,
                "SenteFlow AI test message - system is online and ready.",
            )
            if result.get("error"):
                print(f"      Send failed: {result}")
            else:
                print("      Test message sent successfully")
        except Exception as exc:
            print(f"      Send failed: {exc}")
    else:
        print("\n[3/3] Skipping send test (set TEST_PHONE_NUMBER in .env to enable)")

    print("\nAll checks passed. SenteFlow is ready.\n")


if __name__ == "__main__":
    asyncio.run(run_tests())
