# Open the QLever UI of the graph server on this computer, through Identity-Aware Proxy (no public address).
#   powershell -File graph-server/ui-tunnel.ps1     then open http://localhost:8176
# needs: gcloud logged in with a user that has roles/iap.tunnelResourceAccessor on the project. Ctrl+C closes both tunnels.
$project = 'tib-chat-20261019'
$zone = 'europe-west3-a'
$vm = 'qlever-graph-server'
$sparql = Start-Process gcloud -ArgumentList "compute start-iap-tunnel $vm 7001 --local-host-port=localhost:7001 --zone=$zone --project=$project" -PassThru -NoNewWindow
try {
  Write-Host 'SPARQL endpoint: http://localhost:7001   QLever UI: http://localhost:8176'
  gcloud compute start-iap-tunnel $vm 8176 --local-host-port=localhost:8176 --zone=$zone --project=$project
} finally {
  Stop-Process -Id $sparql.Id -ErrorAction SilentlyContinue
}
