import hashlib
import json
import os
import time
import boto3

TABLE_NAME = os.environ["SETUP_TABLE_NAME"]
table = boto3.resource("dynamodb").Table(TABLE_NAME)

def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body),
    }

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    if method == "POST" and path.endswith("/callback"):
        try:
            body = json.loads(event.get("body") or "{}")
            token = str(body.get("setup_token") or "").strip()
            if len(token) < 24:
                return _response(400, {"error": "invalid setup token"})
            item = {
                "token_hash": _hash(token),
                "status": body.get("status", "connected"),
                "account_id": str(body.get("account_id") or ""),
                "region": str(body.get("region") or ""),
                "role_arn": str(body.get("role_arn") or ""),
                "stack_id": str(body.get("stack_id") or ""),
                "expires_at": int(time.time()) + 3600,
            }
            table.put_item(Item=item)
            return _response(200, {"ok": True})
        except Exception:
            return _response(400, {"error": "invalid callback"})
    if method == "GET" and "/status/" in path:
        token = path.rsplit("/", 1)[-1].strip()
        if len(token) < 24:
            return _response(400, {"error": "invalid setup token"})
        result = table.get_item(Key={"token_hash": _hash(token)}, ConsistentRead=True)
        item = result.get("Item")
        if not item or int(item.get("expires_at", 0)) < int(time.time()):
            return _response(404, {"status": "pending"})
        return _response(200, {
            "status": item.get("status", "connected"),
            "account_id": item.get("account_id", ""),
            "region": item.get("region", ""),
            "role_arn": item.get("role_arn", ""),
            "stack_id": item.get("stack_id", ""),
        })
    return _response(404, {"error": "not found"})
