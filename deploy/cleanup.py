"""Remove only containers belonging to this deployment, including after a crash."""
import os, subprocess
service=os.environ.get('DNSDEMO_SERVICE_ID','dnsdoom-production')
result=subprocess.run(['docker','ps','-aq','--filter','label=dnsdoom.service='+service],capture_output=True,text=True,check=True)
ids=result.stdout.split()
if ids:subprocess.run(['docker','rm','-f',*ids],check=True)
