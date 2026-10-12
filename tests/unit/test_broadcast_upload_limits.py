import re
from pathlib import Path

NGINX_TEMPLATE = Path(__file__).parents[2] / "frontend" / "nginx" / "default.conf.template"
BROADCAST_UPLOAD_LOCATION = re.compile(
    r"location ~ \^/miniapp/api/miniapp/admin/broadcast\(/preview\)\?\$ \{(?P<body>.*?)\n    \}",
    re.DOTALL,
)


def test_only_photo_broadcast_endpoints_raise_the_nginx_body_limit() -> None:
    """Photo broadcasts are up to 10 MiB; nginx's default 1 MiB rejects them with an HTML 413.

    The larger limit must stay scoped to the broadcast upload endpoints, not the server or every API route.
    """
    template = NGINX_TEMPLATE.read_text(encoding="utf-8")

    assert template.count("client_max_body_size") == 1
    assert "client_max_body_size" not in template.split("location", 1)[0]

    match = BROADCAST_UPLOAD_LOCATION.search(template)
    assert match is not None
    block = match.group("body")
    assert "client_max_body_size 12m;" in block
    assert "proxy_pass http://${APP_UPSTREAM};" in block


def test_broadcast_upload_location_takes_precedence_over_generic_miniapp_api() -> None:
    template = NGINX_TEMPLATE.read_text(encoding="utf-8")

    specific = template.index("location ~ ^/miniapp/api/miniapp/admin/broadcast")
    generic = template.index("location /miniapp/api/ {")
    assert specific < generic
