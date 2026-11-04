# OpenBao server settings for the bundled vault (docker-compose.openbao.yml), on integrated (raft) storage.
# OpenBao 2.7 removed the "file" storage that openbao.hcl uses: `bash scripts/openbao.sh upgrade` moves an
# existing vault's data here and sets OPENBAO_CONFIG=openbao-raft.hcl in .env; new vaults start here.
# The port is reachable only from the other containers in this stack, so plain HTTP is used inside that private
# network. Do not publish port 8200 to your LAN without adding TLS.
ui            = true
disable_mlock = true            # containers cannot lock memory without extra privileges

storage "raft" {
  path    = "/openbao/raft"
  node_id = "wa"
}

listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_disable     = true
}

api_addr     = "http://openbao:8200"
cluster_addr = "http://openbao:8201"
