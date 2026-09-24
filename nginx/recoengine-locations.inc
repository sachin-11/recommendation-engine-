# Locations shared by the HTTP and HTTPS servers in nginx.conf.

add_header X-Frame-Options "DENY" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
# Browsers ignore HSTS over plain HTTP; it takes effect once HTTPS is enabled.
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

limit_conn conn 50;

# Internal only: Prometheus reaches app:8000/metrics directly.
location = /metrics {
    return 404;
}

location = /health {
    proxy_pass http://api;
    access_log off;
}

# Password login gets a much tighter limit than the rest of the API.
location = /api/v1/auth/login {
    limit_req zone=auth burst=5 nodelay;
    proxy_pass http://api;
}

location /api/ {
    limit_req zone=api burst=40 nodelay;
    proxy_pass http://api;
}

location / {
    proxy_pass http://dashboard;
}
