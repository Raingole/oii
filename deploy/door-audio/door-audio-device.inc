proxy_pass http://127.0.0.1:18022;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header Authorization $http_authorization;
proxy_set_header Accept-Encoding "";
proxy_buffering off;
proxy_cache off;
gzip off;
