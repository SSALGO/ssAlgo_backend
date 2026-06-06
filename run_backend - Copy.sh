
sudo service nginx start

sudo service nginx restart



source /root/miniconda3/bin/activate


cd /mnt/c/mvp_v48


/root/miniconda3/bin/gunicorn -k eventlet --keyfile=key.pem --certfile=cert.pem --ssl-version=TLSv1_2 -b 0.0.0.0:8443 --timeout 120 backend:app --threads 4
