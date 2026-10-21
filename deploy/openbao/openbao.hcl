# OpenBao server settings for the bundled vault (docker-compose.openbao.yml).
# The port is reachable only from the other containers in this stack, so plain HTTP is used inside
# that private network. Do not publish port 8200 to your LAN without adding TLS.
ui            = true
disable_mlock = true            # containers cannot lock memory without extra privileges

storage "file" {
  path = "/openbao/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}

api_addr = "http://openbao:8200"
