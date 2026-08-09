"""
CLI tool for agents to poll the dashboard for user responses.
"""
import argparse
import json
import sys
import requests

def main():
    parser = argparse.ArgumentParser(description="Agent Poll CLI tool")
    parser.add_argument("--project", required=True, help="Project directory")
    parser.add_argument("--timeout", type=int, default=300, help="Polling timeout in seconds")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--message-id", help="Specific message ID to wait for")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}/api/messages/poll"
    params = {"timeout": args.timeout}
    if args.message_id:
        params["message_id"] = args.message_id

    try:
        response = requests.get(url, params=params, timeout=args.timeout + 5)
        response.raise_for_status()
        data = response.json()
        
        if data.get("status") == "timeout":
            print(json.dumps({"error": "timeout", "message": "Timed out waiting for response"}), file=sys.stderr)
            sys.exit(1)
        else:
            print(json.dumps(data))
            sys.exit(0)
            
    except requests.exceptions.RequestException as e:
        print(json.dumps({"error": "request_failed", "message": str(e)}), file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
