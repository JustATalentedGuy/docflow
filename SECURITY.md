# Security Policy

## Reporting a problem

Please do not open a public issue for a security vulnerability.

Report it privately through GitHub's **Security** tab and **Report a vulnerability** option. Include:

- A short description
- Steps to reproduce the issue
- The affected endpoint or component
- Any suggested fix

## Secrets

Never commit:

- `.env` or `.env.cloud`
- Groq, Hugging Face, or LangSmith keys
- AWS access keys
- EC2 private key files
- Database passwords

If a secret is committed, revoke or rotate it immediately. Removing it from the latest commit is not enough because it may remain in Git history.

## Deployment warning

The AWS instructions describe a temporary portfolio demo. The basic setup uses HTTP and one EC2 instance. A production deployment should use HTTPS, managed secrets, private subnets, automated backups, and stronger session management.
