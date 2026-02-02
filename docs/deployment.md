# Deploy Milvus standalone docker
### Getn Milvus/download milvus
```bash
wget https://github.com/milvus-io/milvus/releases/download/v2.5.14/milvus-standalone-docker-compose.yml -O docker-compose.yml
```


### Start Milvus:
```bash
docker compose up -d
```

### cehck if milvus is working
``` bash
sudo docker compose ps

docker port milvus-standalone 19530/tcp
```

# install Mongodb
```bash
#1 Pull the MongoDB Image:
docker pull mongo:latest

# 2 Create a Docker Volume for Persistent Data:
docker volume create mongodb_data

# 3 Run the MongoDB Container:
docker run -d \
  --name wakilai-mongodb \
  -p 27017:27017 \
  -v mongodb_data:/data/db \
  -e MONGO_INITDB_ROOT_USERNAME=nlp \
  -e MONGO_INITDB_ROOT_PASSWORD=nlp123 \
  mongo:latest
```

# Move ingestion data/inget data
```bash 

pip install gcloud

pip install gcloud-cli

gcloud auth login

gsutil cp -r gs://hbai-general-data/2025/adliya-vazirligi/backups/wakilai.server.backup data/
```

# In case server don't connect to Milvus/Mongodb
```bash
# connect them to server bridge in case they all running on same server i.e  'adliya-network-bridge'
# create a bridge if not exists
docker network create adliya-network-bridge

# attach containers to bridge

docker network connect adliya-network-bridge [container_name]

i.e 

docker network connect adliya-network-bridge adliya-mysql
docker network connect adliya-network-bridge adliya-mongodb
docker network connect adliya-network-bridge milvus-standalone

# Then in .env use like this
MILVUS_URI=http://milvus-standalone:19530/ # Milvus DB url
MONGODB_URI=mongodb://nlp:nlp123@adliya-mongodb:27017/
```
