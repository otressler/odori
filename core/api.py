import json

from django.http import JsonResponse


def read_json(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return None


def error(code, message, status=422, fields=None):
    body = {"error": {"code": code, "message": message}}
    if fields:
        body["error"]["fields"] = fields
    return JsonResponse(body, status=status)
