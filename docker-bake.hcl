variable "DEBIAN_IMAGE" { default = "debian:trixie" }
variable "CI_RESULTS" { default = "build/ci-results" }
variable "CI_ISO" { default = "build/ci-iso" }
variable "ASSEMBLY_ID_1" { default = "" }
variable "ASSEMBLY_ID_2" { default = "" }

group "default" { targets = ["shell", "unit-pipeline", "unit-runtime", "unit-stages", "assembly", "account"] }
group "ci" { targets = ["shell", "unit-pipeline", "unit-runtime", "unit-stages", "assembly", "account"] }
group "container" { targets = ["assembly", "account"] }
group "exports" { targets = ["pair-export-1", "pair-export-2"] }

# Keep test outputs in BuildKit unless a host exporter is itself under test.
target "_common" {
  context = "."
  platforms = ["linux/amd64"]
  args = { DEBIAN_IMAGE = DEBIAN_IMAGE }
  output = ["type=cacheonly"]
}
target "_source" {
  inherits = ["_common"]
  dockerfile = "pipeline/ci.Dockerfile"
}
target "shell" {
  inherits = ["_source"]
  target = "shell"
  no-cache-filter = ["shell"]
}
target "unit-pipeline" {
  inherits = ["_source"]
  target = "unit-pipeline"
  no-cache-filter = ["unit-pipeline"]
}
target "unit-runtime" {
  inherits = ["_source"]
  target = "unit-runtime"
  no-cache-filter = ["unit-runtime"]
}
target "unit-stages" {
  inherits = ["_source"]
  target = "unit-stages"
  no-cache-filter = ["unit-stages"]
}

target "_production" {
  inherits = ["_common"]
  dockerfile = "pipeline/Dockerfile"
}
target "assembly" {
  inherits = ["_production"]
  target = "test-assembly-results"
  no-cache-filter = ["test-assembly"]
  output = ["type=local,dest=${CI_RESULTS},platform-split=false"]
}
target "account" {
  inherits = ["_production"]
  target = "test-account"
  no-cache-filter = ["test-account"]
}
target "_pair-export" {
  inherits = ["_production"]
  target = "pair-artifacts"
  contexts = { iso-input = CI_ISO }
  args = { IMAGE_MIB = "512", ESP_MIB = "64" }
  no-cache-filter = ["pair-assembly"]
}
target "pair-export-1" {
  inherits = ["_pair-export"]
  args = { ASSEMBLY_ID = ASSEMBLY_ID_1 }
  output = ["type=local,dest=${CI_RESULTS}/export-1,platform-split=false"]
}
target "pair-export-2" {
  inherits = ["_pair-export"]
  args = { ASSEMBLY_ID = ASSEMBLY_ID_2 }
  output = ["type=local,dest=${CI_RESULTS}/export-2,platform-split=false"]
}
