# Docflow AWS Cloud Setup Guide

This guide deploys Docflow on one EC2 instance inside a public subnet. PostgreSQL, Redis, Qdrant, FastAPI, Celery, React, and Nginx run as Docker containers on that instance. Amazon S3 stores uploaded documents and Amazon CloudWatch stores container logs.

The target interview statement is:

> Deployed a multi-tenant RAG system on AWS using S3 for document storage, containerized PostgreSQL for metadata, EC2-hosted API and worker services, Qdrant for vector search, and CloudWatch for centralized logs.

## 1. Architecture

```text
AWS VPC
└── Public subnet
    ├── Internet Gateway
    └── EC2 instance with public IPv4/DNS
        ├── Nginx :80                         exposed to users
        ├── React frontend                    Docker network only
        ├── FastAPI API                       Docker network only
        ├── Celery worker                     Docker network only
        ├── PostgreSQL                        Docker network only
        ├── Redis                             Docker network only
        └── Qdrant + persistent volume        Docker network only

External AWS services:
├── Amazon S3          raw uploaded files
└── CloudWatch Logs    container logs
```

### Request and data flow

1. A browser reaches Nginx through the EC2 public DNS name.
2. Nginx serves the React application and proxies `/api`, `/docs`, and `/health` to FastAPI.
3. FastAPI stores relational metadata in the PostgreSQL container.
4. Raw documents are uploaded to a private S3 bucket using the EC2 instance role.
5. FastAPI queues document processing through the Redis container.
6. The Celery worker extracts, cleans, chunks, embeds, and indexes document content.
7. Qdrant stores embeddings and user/file metadata on a persistent Docker volume.
8. Docker sends service logs to CloudWatch through the `awslogs` logging driver.

### Public subnet security model

The EC2 instance is in a public subnet and has a route to an Internet Gateway. This permits browser access and outbound calls to S3, Groq, Hugging Face, LangSmith, and CloudWatch without a NAT Gateway.

Only these host ports are exposed:

- `80`: HTTP access to Nginx.
- `22`: SSH, restricted to your current public IP.

PostgreSQL `5432`, Redis `6379`, Qdrant `6333`, and FastAPI `8000` are not published by the AWS Compose file. They are reachable only through Docker's private bridge network.

## 2. Why this architecture

This design prioritizes a zero-cost, short-lived portfolio demo:

- One EC2 instance avoids load balancer, NAT Gateway, and multi-service compute charges.
- PostgreSQL in Docker avoids a separate managed database charge.
- Redis and Qdrant also run locally on EC2.
- S3 demonstrates managed object storage and keeps documents outside the instance.
- CloudWatch demonstrates centralized observability.
- Docker service boundaries still map cleanly to separate managed services later.

### Tradeoffs

| Decision | Benefit | Limitation |
|---|---|---|
| Single EC2 instance | Cheapest and easiest demo deployment | Single point of failure and limited scaling |
| PostgreSQL container | No separate database service cost | Backups, patching, recovery, and availability are your responsibility |
| Docker volumes | Simple persistent storage on the EC2 EBS disk | Data is lost if the volume is deleted or the instance storage is not preserved |
| Redis container | No managed cache cost | Queue availability depends on the instance |
| Qdrant container | Full vector search without a managed subscription | Scaling and backup are manual |
| Public subnet | No NAT Gateway required | Host must be tightly protected by its security group |
| HTTP only | No certificate/domain cost for a temporary demo | Login tokens travel without transport encryption; do not use real credentials |

For a production system, use private application/data subnets, HTTPS, managed secrets, automated database backups, multiple service replicas, and independently scalable compute.

## 3. Cost controls

AWS pricing and Free Tier eligibility vary by account and creation date. Zero out-of-pocket cost is only achievable when the selected resources remain inside your account's credits or eligible limits.

Before deployment:

1. Open **Billing and Cost Management**.
2. Create a `$1` cost budget.
3. Add alerts at 50%, 80%, and 100%.
4. Use a single AWS region for every resource.
5. Select an EC2 instance that your account explicitly marks Free Tier eligible.
6. Keep the deployment running only for testing, screenshots, and recording.
7. Follow the teardown checklist immediately afterward.

Avoid:

- NAT Gateway
- Application Load Balancer
- Fargate
- Elastic IP unless absolutely required
- Large EBS volumes or snapshots
- Long CloudWatch retention

Official references:

- AWS Free Tier: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html
- EC2 Free Tier usage: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html
- S3 pricing: https://aws.amazon.com/s3/pricing/
- CloudWatch pricing: https://aws.amazon.com/cloudwatch/pricing/

## 4. Repository support

The cloud deployment uses:

- `.env.cloud.example`: environment template.
- `infra/docker-compose.aws.yml`: EC2 container topology.
- `infra/nginx.conf`: frontend and API routing.
- `app/db.py`: SQLite locally and PostgreSQL when `DATABASE_URL` is PostgreSQL.
- `app/storage/s3.py`: MinIO locally and instance-role authenticated S3 in AWS.

The AWS Compose stack contains:

| Service | Container role | Persistent state |
|---|---|---|
| `nginx` | Public reverse proxy | None |
| `frontend` | React static application | None |
| `api` | FastAPI endpoints | None |
| `worker` | Celery ingestion pipeline | None |
| `postgres` | Users, sessions, files, chats, messages | `postgres_data` volume |
| `redis` | Celery broker and job status | Ephemeral |
| `qdrant` | Vectors and chunk payloads | `qdrant_storage` volume |

## 5. Create the AWS network

You may use the default VPC for a short demo, but creating a small dedicated VPC gives a clearer cloud story.

### VPC

Create:

- Name: `docflow-demo-vpc`
- IPv4 CIDR: `10.20.0.0/16`

### Public subnet

Create:

- Name: `docflow-public-subnet`
- CIDR: `10.20.1.0/24`
- Availability Zone: any zone in your chosen region
- Auto-assign public IPv4: enabled

### Internet access

1. Create an Internet Gateway named `docflow-demo-igw`.
2. Attach it to `docflow-demo-vpc`.
3. Create a route table named `docflow-public-rt`.
4. Add route `0.0.0.0/0 -> docflow-demo-igw`.
5. Associate the route table with `docflow-public-subnet`.

No NAT Gateway is required because the EC2 instance is directly in the public subnet.

## 6. Create S3 storage

1. Open **S3 -> Create bucket**.
2. Name it `docflow-demo-yourname`.
3. Use the same region as EC2.
4. Keep **Block all public access** enabled.
5. Enable SSE-S3 default encryption.
6. Leave versioning disabled for the temporary demo.

Objects are stored using this pattern:

```text
uploads/{user_id}/{job_id}/{filename}
```

The bucket is private. The app accesses it through the EC2 IAM role.

## 7. Create the EC2 IAM role

An IAM role contains two different policies:

1. **Trust policy:** defines who may assume the role. In this case, EC2.
2. **Permissions policy:** defines what the role may do. In this case, access the Docflow S3 bucket and write CloudWatch logs.

Do not paste the permissions policy into the custom trust-policy editor.

### 7.1 Create the role and trust EC2

The simplest console path is:

1. Open **IAM -> Roles -> Create role**.
2. Under **Trusted entity type**, select **AWS service**.
3. Under **Use case**, select **EC2**.
4. Choose **Next**.
5. Do not select broad managed policies such as `AmazonS3FullAccess`.
6. Choose **Next**.
7. Role name: `docflow-demo-ec2-role`.
8. Choose **Create role**.

AWS creates the EC2 trust relationship and instance profile automatically.

If you prefer **Custom trust policy**, paste only this JSON:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

This trust policy intentionally contains:

- A `Principal` identifying EC2.
- The `sts:AssumeRole` action.
- No `Resource` element.
- No S3 or CloudWatch permissions.

After creating the role, verify it:

1. Open **IAM -> Roles -> docflow-demo-ec2-role**.
2. Open **Trust relationships**.
3. Confirm the trusted principal is `ec2.amazonaws.com`.

### 7.2 Add the S3 and CloudWatch permissions

Now add a separate permissions policy:

1. Open **IAM -> Roles -> docflow-demo-ec2-role**.
2. Open the **Permissions** tab.
3. Choose **Add permissions -> Create inline policy**.
4. Select the **JSON** editor.
5. Paste the policy below after replacing both occurrences of `docflow-demo-yourname`.
6. Choose **Next**.
7. Policy name: `DocflowDemoS3AndLogsPolicy`.
8. Choose **Create policy**.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DocumentBucketObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::docflow-demo-s3/*"
    },
    {
      "Sid": "DocumentBucketMetadata",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": "arn:aws:s3:::docflow-demo-s3"
    },
    {
      "Sid": "ContainerLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "*"
    }
  ]
}
```

This permissions policy contains `Resource` elements because it controls access to S3 and CloudWatch resources. It does not contain `Principal`; the role itself is the identity receiving these permissions.

### 7.3 Attach the role to EC2

If the instance has not been launched yet, select `docflow-demo-ec2-role` under **Advanced details -> IAM instance profile** during launch.

For an existing instance:

1. Open **EC2 -> Instances**.
2. Select the Docflow instance.
3. Choose **Actions -> Security -> Modify IAM role**.
4. Select `docflow-demo-ec2-role`.
5. Choose **Update IAM role**.

After connecting to EC2, verify that temporary role credentials are available:

```bash
TOKEN=$(curl -sS -X PUT \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" \
  http://169.254.169.254/latest/api/token)

curl -sS \
  -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/iam/security-credentials/
```

The command should print:

```text
docflow-demo-ec2-role
```

Do not store long-lived AWS access keys in `.env.cloud`.

## 8. Create CloudWatch logging

Create a log group:

```text
/docflow/demo
```

Set retention to one or three days. The AWS Compose file creates separate log streams for API, worker, frontend, PostgreSQL, Redis, Qdrant, and Nginx.

## 9. Launch EC2

Recommended settings:

- AMI: Amazon Linux 2023 x86_64
- Instance: an x86 instance with 4 GB RAM is recommended; use account credits and terminate it immediately after the demo
- EBS: 20-30 GB gp3 within your account's allowance
- Subnet: `docflow-public-subnet`
- Public IPv4: enabled
- IAM role: `docflow-demo-ec2-role`

The API and worker each load ML dependencies, so a 1 GB micro instance is not a reliable target. A 2 GB instance may work with swap and small PDFs, while 4 GB is the safer recording configuration. Strict legacy Free Tier eligibility may therefore be insufficient; use promotional credits or run the instance only for the short demo window.

### Security group

Inbound:

| Port | Source | Purpose |
|---|---|---|
| 22 | Your public IP `/32` | SSH |
| 80 | Your public IP `/32`, or temporarily `0.0.0.0/0` | Demo UI |

Outbound:

- Allow all outbound traffic for S3 and external model APIs.

Do not add inbound rules for `5432`, `6379`, `6333`, or `8000`.

### Ubuntu installation

Use these commands when the SSH prompt starts with `ubuntu@...`:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Apply the new Docker group in the current SSH session:

```bash
newgrp docker
```

Verify:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

If `newgrp docker` closes or changes the shell unexpectedly, disconnect with `exit`, reconnect over SSH, and run the verification commands again.

### Amazon Linux 2023 installation

Use these commands only when the SSH user is `ec2-user`:

```bash
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user

sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL \
  https://github.com/docker/compose/releases/download/v5.1.2/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

Log out and reconnect, then verify:

```bash
docker --version
docker compose version
```

### Optional swap for small instances

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
```

## 10. Deploy the application

### 10.1 Publish the project to GitHub from Windows

The commands in this subsection run in **Windows PowerShell on your computer**, not on EC2.

Git and GitHub CLI are already installed if these commands print versions:

```powershell
git --version
gh --version
```

Open PowerShell and move to the project:

```powershell
cd C:\Coding\docflow
```

#### Configure your Git identity

Use the name and email associated with your GitHub account:

```powershell
git config --global user.name "Your Name"
git config --global user.email "your-email@example.com"
```

Verify:

```powershell
git config --global --list
```

#### Confirm secrets will not be committed

The repository contains a `.gitignore` that excludes:

- `.env`
- `.env.cloud`
- local SQLite databases
- Python caches
- `frontend/node_modules`
- frontend build output

Keep `.env.example` and `.env.cloud.example`; they contain placeholders and document the required settings.

If `.env` or `.env.cloud` contains real keys, never rename it to an example file.

#### Initialize Git

```powershell
git init
git branch -M main
```

Review the files Git sees:

```powershell
git status
```

Check specifically that secret files are ignored:

```powershell
git check-ignore -v .env
git check-ignore -v .env.cloud
```

If a file does not exist, `git check-ignore` may print nothing. That is fine; the `.gitignore` patterns still protect it when created.

Create the first commit:

```powershell
git add .
git status
git commit -m "Initial Docflow application with AWS deployment"
```

Before committing, inspect the `git status` output and verify that `.env` and `.env.cloud` are not listed.

#### Sign in to GitHub CLI

```powershell
gh auth login
```

Choose:

1. `GitHub.com`
2. `HTTPS`
3. `Login with a web browser`

GitHub CLI displays a one-time code and opens a browser. Enter the code and authorize it.

Verify:

```powershell
gh auth status
```

#### Create and push the GitHub repository

For a public portfolio repository:

```powershell
gh repo create docflow --public --source . --remote origin --push
```

For a private repository, replace `--public` with `--private`. A private repository requires GitHub authentication when cloning it on EC2.

Verify the remote and branch:

```powershell
git remote -v
git status
```

Open the repository:

```powershell
gh repo view --web
```

Inspect the GitHub file list and confirm that neither `.env` nor `.env.cloud` appears.

#### Push later changes

After changing the project:

```powershell
cd C:\Coding\docflow
git status
git add .
git commit -m "Describe the change"
git push
```

### 10.2 Connect to EC2

The remaining commands run on the EC2 Linux instance.

SSH into EC2:

```powershell
ssh -i your-key.pem ec2-user@YOUR_EC2_PUBLIC_DNS
```

On Windows, the key may be in Downloads:

```powershell
ssh -i "$HOME\Downloads\your-key.pem" ec2-user@YOUR_EC2_PUBLIC_DNS
```

If Windows reports that the private key permissions are too open:

```powershell
icacls "$HOME\Downloads\your-key.pem" /inheritance:r
icacls "$HOME\Downloads\your-key.pem" /grant:r "$($env:USERNAME):(R)"
```

Then retry SSH.

### 10.3 Clone and configure Docflow on EC2

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/docflow.git
cd docflow
```

Create the cloud environment:

```bash
cp .env.cloud.example .env.cloud
nano .env.cloud
```

Set:

```bash
AWS_REGION=us-east-1
S3_BUCKET_NAME=docflow-demo-yourname
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_ENDPOINT_URL=

POSTGRES_DB=docflow
POSTGRES_USER=docflow_user
POSTGRES_PASSWORD=USE_A_LONG_RANDOM_PASSWORD
DATABASE_URL=postgresql://docflow_user:USE_A_LONG_RANDOM_PASSWORD@postgres:5432/docflow

GROQ_API_KEY=your_groq_key
HF_API_TOKEN=your_huggingface_token
LANGCHAIN_API_KEY=your_langsmith_key
```

The hostname in `DATABASE_URL` must remain `postgres`, which is the Docker Compose service name.

For the simplest setup, use a URL-safe PostgreSQL password containing letters, numbers, underscores, or hyphens. Otherwise URL-encode special characters in `DATABASE_URL`.

Protect the environment file:

```bash
chmod 600 .env.cloud
```

Start the stack:

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml up --build -d
```

Inspect it:

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml ps
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml logs -f api worker postgres
```

Open:

```text
http://YOUR_EC2_PUBLIC_DNS
http://YOUR_EC2_PUBLIC_DNS/docs
```

Health check:

```bash
curl http://YOUR_EC2_PUBLIC_DNS/health
```

Expected:

```json
{"status":"ok","version":"1.0.0"}
```

### Updating the EC2 deployment later

After pushing changes from Windows:

```bash
cd ~/docflow
git pull
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml up --build -d
```

## 11. Persistence and backups

PostgreSQL and Qdrant data live in Docker named volumes backed by the EC2 EBS volume:

```text
postgres_data
qdrant_storage
```

Container recreation does not delete them:

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml down
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml up -d
```

Using `down -v` deletes both volumes and their data.

### PostgreSQL backup

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml exec -T postgres \
  pg_dump -U docflow_user -d docflow > docflow-backup.sql
```

### PostgreSQL restore

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml exec -T postgres \
  psql -U docflow_user -d docflow < docflow-backup.sql
```

For a longer-lived deployment, copy backups to a separate private S3 prefix and use an EBS snapshot policy. For the temporary demo, create one backup before recording and delete it during teardown.

## 12. Demo and screenshot workflow

Use a small PDF to reduce CPU and memory pressure.

1. Show the EC2 instance in `docflow-public-subnet`.
2. Show its security group exposing only ports 22 and 80.
3. Open the React UI through the EC2 public DNS.
4. Register a user.
5. Upload a PDF and wait for `completed`.
6. Show the object in the private S3 bucket.
7. Create a chat and ask a document question.
8. Ask a follow-up such as "Give an example for it."
9. Show source snippets in the UI.
10. Open CloudWatch and show API/worker/PostgreSQL streams.
11. On EC2, show `docker compose ps` to demonstrate the service topology.

Useful screenshots:

- VPC public subnet and route table
- EC2 instance and security group
- Docker service status
- Private S3 objects
- CloudWatch log streams
- React file management and chat interface
- Swagger API documentation

## 13. Interview explanation

### Short version

> I deployed Docflow as a multi-tenant RAG application on one AWS EC2 instance in a public subnet. Nginx exposes the React UI and FastAPI API, while PostgreSQL, Redis, Qdrant, and Celery run on a private Docker network on the same host. Uploaded documents are stored in private S3, and container logs are centralized in CloudWatch. I chose this topology to demonstrate AWS networking, IAM, S3, observability, containers, relational storage, and vector retrieval while staying within a zero-cost demo budget.

### Design-decision version

> I separated state by access pattern. S3 stores large immutable document objects. PostgreSQL stores transactional user, file, chat, and session metadata. Qdrant stores retrieval-optimized vectors and user-scoped payloads. Redis stores short-lived queue and job state. For the portfolio deployment I colocated the stateful containers on one EC2 instance to avoid separate service costs. The tradeoff is a single failure domain and manual backups, but each container boundary can later move to independently managed infrastructure.

### Resume bullets

- Deployed a multi-tenant RAG system on AWS using private S3 object storage, containerized PostgreSQL metadata storage, EC2-hosted API/worker/vector services, and CloudWatch logging.
- Designed a zero-cost AWS demo topology in a VPC public subnet, exposing only Nginx while isolating PostgreSQL, Redis, Qdrant, and FastAPI on a private Docker network.
- Implemented user-scoped hybrid retrieval with Qdrant, BM25, asynchronous Celery ingestion, PostgreSQL chat history, and S3 document persistence.

## 14. Troubleshooting

### PostgreSQL is unhealthy

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml logs postgres
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml exec postgres \
  pg_isready -U docflow_user -d docflow
```

Check that `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` match `DATABASE_URL`.

If you changed the credentials after PostgreSQL initialized, the existing volume still contains the old credentials. For an empty demo database only:

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml down -v
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml up --build -d
```

### `/docs` returns 502

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml ps
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml logs api nginx postgres
```

The API waits for PostgreSQL and Redis health checks. Look for database authentication errors or an out-of-memory termination.

### Upload receives S3 access denied

Verify:

- The EC2 IAM role is attached.
- The IAM policy contains the exact bucket ARN.
- `AWS_ENDPOINT_URL` is blank.
- AWS access-key variables are blank so boto3 uses the instance role.

### CloudWatch streams are missing

Verify:

- `/docflow/demo` exists.
- The EC2 role permits `logs:CreateLogStream` and `logs:PutLogEvents`.
- The region used by Docker matches the log-group region.

### Worker is killed or very slow

- Keep Celery concurrency at `1`.
- Add swap.
- Use smaller PDFs.
- Stop nonessential containers during image builds if memory is tight.
- Choose a larger credit-eligible EC2 instance for the recording.

### Docker build fails with `No space left on device`

The Python backend image includes PyTorch, sentence-transformers, OCR libraries, and document-processing dependencies. An 8 GB root volume is usually too small for the operating system, Docker images, temporary build layers, PostgreSQL, and Qdrant.

The AWS Compose file builds one shared backend image for both API and worker and skips model preloading, but the instance should still have a 20-30 GB gp3 root volume.

Check current usage:

```bash
df -h
lsblk
docker system df
```

Clean failed build cache:

```bash
docker builder prune -af
docker image prune -af
sudo apt-get clean
```

Do not run `docker volume prune` or `docker compose down -v` after PostgreSQL/Qdrant contain data.

If the root EBS volume is smaller than 20 GB:

1. Open **EC2 -> Instances -> select the instance**.
2. Open the **Storage** tab.
3. Select the root EBS volume ID.
4. Choose **Actions -> Modify volume**.
5. Set size to `30 GiB` gp3.
6. Confirm the modification.
7. Wait until the modification state is `optimizing` or `completed`.

Back on the Ubuntu instance, identify the root partition:

```bash
findmnt /
lsblk
```

Install the partition growth utility:

```bash
sudo apt-get update
sudo apt-get install -y cloud-guest-utils
```

For the common NVMe layout where root is `/dev/nvme0n1p1`:

```bash
sudo growpart /dev/nvme0n1 1
sudo resize2fs /dev/nvme0n1p1
```

For the common Xen layout where root is `/dev/xvda1`:

```bash
sudo growpart /dev/xvda 1
sudo resize2fs /dev/xvda1
```

If `findmnt -no FSTYPE /` reports `xfs`, use this instead of `resize2fs`:

```bash
sudo xfs_growfs -d /
```

Verify the expanded filesystem:

```bash
df -h /
```

Retry the deployment:

```bash
cd ~/docflow
git pull
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml up --build -d
```

## 15. Teardown

To stop containers while preserving database/vector data:

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml down
```

To delete local container data:

```bash
docker compose --env-file .env.cloud -f infra/docker-compose.aws.yml down -v
```

Then:

1. Terminate the EC2 instance.
2. Delete any remaining EBS volumes and snapshots.
3. Empty and delete the S3 bucket.
4. Delete `/docflow/demo` from CloudWatch Logs.
5. Delete the IAM role/policy if no longer needed.
6. Delete the security group, route table, subnet, Internet Gateway, and VPC.
7. Release any Elastic IP if one was allocated.
8. Check Billing and Free Tier usage.
