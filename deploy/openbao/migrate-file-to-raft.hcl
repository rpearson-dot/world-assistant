# Used once by `bash scripts/openbao.sh upgrade`: copies the vault from file storage to raft storage
# (`bao operator migrate`), with the server stopped. The file folder is only read.
storage_source "file" {
  path = "/openbao/file"
}

storage_destination "raft" {
  path    = "/openbao/raft"
  node_id = "wa"
}

cluster_addr = "http://127.0.0.1:8201"
