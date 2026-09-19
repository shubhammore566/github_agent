# GitHub Agent — GitHub OAuth + passwordless AWS OIDC

This version is designed as a multi-user public app. A visitor connects **their own GitHub account** and, when they choose AWS, connects **their own AWS account** without pasting an AWS Access Key or Secret Key.

## User experience

1. Click **Connect GitHub**.
2. GitHub opens its own authorization screen.
3. The user signs in to their GitHub account and approves the app.
4. The Agent receives that user's GitHub authorization only for that browser session.
5. The user says things naturally, for example:
   - `push this project`
   - `create a private repo and push it`
   - `deploy this project to AWS`
   - `check my EC2 disk`
   - `free some space`
   - `increase the disk by 10 GB`
6. GitHub write/destructive actions still go through the existing approval gate.

## GitHub security

The public app must have one GitHub OAuth App configured on the **server**:

- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `PUBLIC_APP_URL`

Users do **not** see or enter those values. They also do not paste a personal GitHub PAT.

The OAuth Client ID identifies your application; it does not make every visitor use the developer's GitHub account. After authorization, GitHub returns authorization for the person who just signed in. The app keeps that access in that user's session.

For production, a GitHub App with fine-grained permissions is an even stronger long-term option than a classic OAuth App.

## Passwordless AWS connection

There is intentionally **no AWS Access Key ID / Secret Access Key field**.

The Agent uses this architecture:

```text
User's GitHub account
        |
        v
GitHub Agent
        |
        | GitHub Actions workflow
        v
GitHub OIDC token
        |
        v
User's AWS IAM OIDC role
        |
        v
User's AWS account
        |
        +--> EC2 / SSM / EBS
```

GitHub documents OIDC for AWS specifically so workflows can access AWS without long-lived AWS credentials. AWS IAM can restrict the trust policy to a particular repository and branch. This is the mechanism used here.

### One-time AWS setup (visitor)

After the user connects GitHub and selects/pushes a repository:

1. In **Deployment Control Center → AWS EC2**, tick either checkbox only if it
   applies ("already have an OIDC provider" / "already have an EC2
   instance") — most first-time visitors leave both unticked.
2. Click **Setup AWS** (or download the template manually).
3. Sign in to the **user's own AWS account** (or create a free one).
4. Acknowledge that the stack creates IAM resources, then **Create stack**.
   By default the stack creates the GitHub OIDC provider, the dedicated IAM
   role, **and** a small free-tier-eligible EC2 instance (t3.micro) tagged
   `Name=github_App` with Docker/Git and the SSM agent already wired up — so
   a first-time visitor does not need to provision anything by hand first.
5. Wait ~30-60 seconds for `CREATE_COMPLETE`.
6. Copy the account's **12-digit Account ID** (shown top-right in the AWS
   console). The role this stack creates is always named
   `GitHubAgentDeploymentRole`, so the account ID alone is enough — the app
   builds the full Role ARN itself. (A visitor who prefers to paste the
   whole ARN from the Outputs tab can still do that instead.)
7. Paste that Account ID into the Agent and click **Connect**.
8. The Agent installs `.github/workflows/github-agent-aws.yml` into the user's repository.
9. From then on, saying `deploy this project to AWS` triggers the workflow. AWS credentials are short-lived inside that GitHub Actions run.

AWS CloudFormation supports uploading a template file from the console, and the template in this project creates the GitHub OIDC provider, a dedicated IAM role, and (optionally) the EC2 host itself.

### One-time AWS setup (developer) — optional, enables the 1-click Connect button

By default, a visitor connects AWS by downloading `github-agent-aws-oidc.yaml`
from the sidebar and manually uploading it in the AWS Console. You (the
developer) can replace that with a single **"Open AWS & create the role"**
button that takes the visitor straight from sign-in to a pre-filled
Create-stack page. AWS's quick-create deep links only work when the template
is hosted in an S3 bucket — that's an AWS Console requirement, not something
this app can work around — so this is a one-time setup on your own AWS
account:

1. Create an S3 bucket (any name, any region) — or reuse one you already have.
2. Upload `aws/github-agent-aws-oidc.yaml` from this repo to that bucket, with
   **public read** access on that one object (the template file itself is not
   sensitive — it only describes what CloudFormation *would* create; it does
   not grant any access by existing, and it contains no account-specific data).
3. Copy the object's HTTPS URL, e.g.
   `https://your-bucket.s3.us-east-1.amazonaws.com/github-agent-aws-oidc.yaml`.
4. Set it as the `AWS_OIDC_TEMPLATE_URL` environment variable wherever you run
   this app (see `.env.example`).

Once set, every visitor's "Connect my AWS account" step becomes: click the
button → AWS opens and asks them to sign in (or create a free account) →
they land directly on **their own** account's Create-stack page, already
filled in with their repo/branch → tick the IAM acknowledgement box → Create
→ copy the `GitHubAgentRoleArn` output → paste it back into the Agent. AWS
gives no way to skip that last copy-paste step automatically (CloudFormation
has no callback to hand the output straight back to a third-party app), so
this one paste is the only manual step left.

If `AWS_OIDC_TEMPLATE_URL` is not set, the sidebar automatically falls back to
the original download-then-upload flow — nothing breaks, it's just one extra
manual step for the visitor.

### Automatic AWS callback (no Account ID / ARN copy-paste)

For a product-style onboarding flow, the developer can also deploy the small
`aws_setup_service/` stack once. It creates an API Gateway HTTP API, a Lambda
callback handler, and a pay-per-request DynamoDB table for short-lived setup
sessions.

Set the resulting `POST /callback` URL as `AWS_SETUP_CALLBACK_URL`. When both
`AWS_OIDC_TEMPLATE_URL` and `AWS_SETUP_CALLBACK_URL` are configured, the Agent:

1. Generates a temporary browser-session setup token.
2. Opens the AWS CloudFormation quick-create page with the token and callback
   URL pre-filled.
3. The visitor only signs in/authorizes the CloudFormation stack and clicks
   **Create stack**.
4. CloudFormation invokes the callback Lambda after creating the role.
5. The callback service stores the account ID + role ARN for one hour.
6. The Agent polls that short-lived session and automatically marks AWS as
   connected.

The visitor does **not** paste an Account ID, Role ARN, JSON policy, YAML file,
or trust relationship. The token is temporary and is stored hashed in
DynamoDB. The callback service does not receive AWS credentials.

Deploy the callback service once from `aws_setup_service/template.yaml` using
AWS SAM, then configure:

```text
AWS_OIDC_TEMPLATE_URL=https://...s3.../github-agent-aws-oidc.yaml
AWS_SETUP_CALLBACK_URL=https://...execute-api.../callback
```

The manual Account ID/Role ARN connection remains available as a fallback if
the callback service is not configured or temporarily unavailable.



The role is deliberately **not AdministratorAccess**. It grants only the current EC2 deployment/maintenance surface:

- EC2 instance/security-group/volume read
- `ssm:SendCommand` restricted to instances tagged `Name=github_App` (via an
  `ssm:resourceTag/Name` condition) plus the `AWS-RunShellScript` document —
  it cannot be used to run commands on any other EC2 instance in the account
- `ssm:GetCommandInvocation` / `ssm:DescribeInstanceInformation` (read-only)
- EBS volume growth
- Security-group ingress restricted to security groups tagged `Name=github_App`
- STS identity check

The Agent does **not** automatically modify IAM permissions when an AWS call fails. If a future feature needs S3, ECS, Lambda, RDS, etc., that service's permissions should be explicitly added to the role instead of silently granting administrator access.

### EC2 requirement

The AWS deployment path targets a running EC2 instance managed by Systems Manager (SSM). It uses the instance tagged `Name=github_App` when no instance ID is supplied, or the user can provide an instance ID.

By default the CloudFormation stack creates this instance for the visitor
(Amazon Linux 2023, t3.micro, a dedicated instance profile with
`AmazonSSMManagedInstanceCore` so SSM registration works out of the box, and
Docker/Git installed via UserData) — nothing needs to be provisioned by hand
first. A visitor who already has a suitable instance can tick "I already
have my own EC2 instance ready" in the sidebar before creating the stack
(`CreateEC2Instance=false`), in which case their own instance just needs the
normal Systems Manager registration (an instance profile with
`AmazonSSMManagedInstanceCore` and the SSM Agent running — most Amazon Linux
AMIs already include it) and the `Name=github_App` tag.

An AWS account can only have **one** OIDC provider for
`token.actions.githubusercontent.com`. A visitor who has run this kind of
setup before (this app or a similar one) should tick "This AWS account
already has a GitHub Actions OIDC provider" so the stack doesn't try to
create a duplicate and fail (`CreateOIDCProvider=false`).

## What happens during deployment

The Agent installs/updates a repository-local GitHub Actions workflow and triggers it with the connected user's GitHub authorization.

The workflow:

1. Requests a short-lived GitHub OIDC token.
2. Exchanges it for short-lived AWS credentials for **the user's AWS role**.
3. Finds the target EC2 instance.
4. Uses SSM to clone/pull the user's repository with GitHub's ephemeral workflow token.
5. Builds Docker.
6. Starts the container.
7. Finds a free host port when needed.
8. Opens that port in the selected security group.
9. Returns the live URL to the Agent.

No developer AWS access key is embedded in the application.

## Files

- `app.py` — Streamlit UI and natural-language agent
- `github_agent/aws_oidc.py` — AWS OIDC setup, workflow generation, dispatch and result retrieval
- `aws/github-agent-aws-oidc.yaml` — example CloudFormation setup
- `aws/github-agent-aws-workflow.yml` — example generated workflow
- `aws/agent-deployer-policy.json` — reference least-privilege policy
- `.env.example` — server-side environment configuration

## Run

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

For a public deployment, configure the GitHub OAuth variables as platform secrets/environment variables. Never commit real Client Secrets, AWS keys, GitHub PATs, or AI API keys.
