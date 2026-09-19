"""Passwordless AWS integration for GitHub Agent."""
from __future__ import annotations
import io, time, zipfile, re
from datetime import datetime
from typing import Any, Dict, Tuple
from urllib.parse import quote
import requests

WORKFLOW_PATH = ".github/workflows/github-agent-aws.yml"
CLOUDFORMATION_TEMPLATE = "AWSTemplateFormatVersion: '2010-09-09'\nDescription: GitHub Agent - passwordless AWS deployment access via GitHub Actions OIDC, with an optional self-provisioned EC2 host\nParameters:\n  GitHubOwner:\n    Type: String\n    Default: __OWNER__\n  GitHubRepo:\n    Type: String\n    Default: '*'\n    Description: Leave as '*' to allow every repo under GitHubOwner to deploy with this role, or set one exact repo name to restrict it.\n  GitHubBranch:\n    Type: String\n    Default: '*'\n    Description: Leave as '*' to allow any branch, or set one exact branch name to restrict it.\n  CreateOIDCProvider:\n    Type: String\n    Default: 'true'\n    AllowedValues: ['true', 'false']\n    Description: Set to 'false' if this AWS account already has a GitHub Actions OIDC provider (token.actions.githubusercontent.com) - an AWS account can only have one.\n  CreateEC2Instance:\n    Type: String\n    Default: 'true'\n    AllowedValues: ['true', 'false']\n    Description: Set to 'false' if you already have your own EC2 instance (tagged Name=github_App, with the SSM agent running) and do not want this stack to create a new one.\n  InstanceType:\n    Type: String\n    Default: t3.micro\n    Description: Only used when CreateEC2Instance is true. t3.micro / t2.micro are AWS Free Tier eligible for the first 12 months.\n  SetupToken:\n    Type: String\n    Default: ''\n    NoEcho: true\n    Description: Temporary token used only to complete automatic setup in the GitHub Agent.\n  CallbackURL:\n    Type: String\n    Default: ''\n    Description: HTTPS endpoint used by the one-time setup callback. Leave blank to use manual connection.\nConditions:\n  ShouldCreateOIDCProvider: !Equals [!Ref CreateOIDCProvider, 'true']\n  ShouldCreateEC2: !Equals [!Ref CreateEC2Instance, 'true']\n  ShouldCallback: !And [!Not [!Equals [!Ref SetupToken, '']], !Not [!Equals [!Ref CallbackURL, '']]]\nResources:\n  GitHubActionsOIDCProvider:\n    Type: AWS::IAM::OIDCProvider\n    Condition: ShouldCreateOIDCProvider\n    Properties:\n      Url: https://token.actions.githubusercontent.com\n      ClientIdList: [sts.amazonaws.com]\n      ThumbprintList: [6938fd4d98bab03faadb97b34396831e3780aea1]\n  GitHubAgentDeploymentRole:\n    Type: AWS::IAM::Role\n    Properties:\n      RoleName: GitHubAgentDeploymentRole\n      Description: Short-lived role for GitHub Agent deployments\n      AssumeRolePolicyDocument:\n        Version: '2012-10-17'\n        Statement:\n          - Effect: Allow\n            Principal:\n              Federated: !Sub 'arn:${AWS::Partition}:iam::${AWS::AccountId}:oidc-provider/token.actions.githubusercontent.com'\n            Action: sts:AssumeRoleWithWebIdentity\n            Condition:\n              StringEquals:\n                token.actions.githubusercontent.com:aud: sts.amazonaws.com\n              StringLike:\n                token.actions.githubusercontent.com:sub: !Sub 'repo:${GitHubOwner}/${GitHubRepo}:ref:refs/heads/${GitHubBranch}'\n      Policies:\n        - PolicyName: GitHubAgentEC2Deployment\n          PolicyDocument:\n            Version: '2012-10-17'\n            Statement:\n              - Effect: Allow\n                Action:\n                  - ec2:DescribeInstances\n                  - ec2:DescribeSecurityGroups\n                  - ec2:DescribeVolumes\n                  - ec2:DescribeVolumesModifications\n                Resource: '*'\n              - Effect: Allow\n                Action: ec2:AuthorizeSecurityGroupIngress\n                Resource: !Sub 'arn:${AWS::Partition}:ec2:*:${AWS::AccountId}:security-group/*'\n                Condition:\n                  StringEquals:\n                    aws:ResourceTag/Name: github_App\n              - Effect: Allow\n                Action: ec2:ModifyVolume\n                Resource: '*'\n              - Effect: Allow\n                Action: ssm:SendCommand\n                Resource: !Sub 'arn:${AWS::Partition}:ec2:*:${AWS::AccountId}:instance/*'\n                Condition:\n                  StringEquals:\n                    ssm:resourceTag/Name: github_App\n              - Effect: Allow\n                Action: ssm:SendCommand\n                Resource:\n                  - !Sub 'arn:${AWS::Partition}:ssm:*::document/AWS-RunShellScript'\n              - Effect: Allow\n                Action:\n                  - ssm:GetCommandInvocation\n                  - ssm:DescribeInstanceInformation\n                Resource: '*'\n              - Effect: Allow\n                Action: sts:GetCallerIdentity\n                Resource: '*'\n  GitHubAgentSecurityGroup:\n    Type: AWS::EC2::SecurityGroup\n    Condition: ShouldCreateEC2\n    Properties:\n      GroupDescription: GitHub Agent host - app ports are opened dynamically by the Agent itself at deploy time\n      SecurityGroupIngress:\n        - IpProtocol: tcp\n          FromPort: 22\n          ToPort: 22\n          CidrIp: 0.0.0.0/0\n          Description: Optional SSH access (AWS Systems Manager Session Manager also works without this)\n      Tags:\n        - Key: Name\n          Value: github_App\n  GitHubAgentInstanceRole:\n    Type: AWS::IAM::Role\n    Condition: ShouldCreateEC2\n    Properties:\n      AssumeRolePolicyDocument:\n        Version: '2012-10-17'\n        Statement:\n          - Effect: Allow\n            Principal:\n              Service: ec2.amazonaws.com\n            Action: sts:AssumeRole\n      ManagedPolicyArns:\n        - arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore\n  GitHubAgentInstanceProfile:\n    Type: AWS::IAM::InstanceProfile\n    Condition: ShouldCreateEC2\n    Properties:\n      Roles:\n        - !Ref GitHubAgentInstanceRole\n  GitHubAgentEC2Instance:\n    Type: AWS::EC2::Instance\n    Condition: ShouldCreateEC2\n    Properties:\n      InstanceType: !Ref InstanceType\n      ImageId: '{{resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64}}'\n      IamInstanceProfile: !Ref GitHubAgentInstanceProfile\n      SecurityGroupIds:\n        - !GetAtt GitHubAgentSecurityGroup.GroupId\n      BlockDeviceMappings:\n        - DeviceName: /dev/xvda\n          Ebs:\n            VolumeSize: 20\n            VolumeType: gp3\n      UserData:\n        Fn::Base64: |\n          #!/bin/bash\n          dnf install -y docker git || yum install -y docker git\n          systemctl enable --now docker\n          usermod -aG docker ec2-user\n      Tags:\n        - Key: Name\n          Value: github_App\n  AwsSetupCallbackFunction:\n    Type: AWS::Lambda::Function\n    Condition: ShouldCallback\n    DependsOn: GitHubAgentDeploymentRole\n    Properties:\n      Runtime: python3.12\n      Handler: index.handler\n      Timeout: 15\n      Role: !GetAtt AwsSetupCallbackRole.Arn\n      Code:\n        ZipFile: |\n          import json, urllib.request\n          def send_response(event, context, status, reason=''):\n              body = {\n                  'Status': status,\n                  'Reason': reason or 'See CloudWatch logs',\n                  'PhysicalResourceId': event.get('PhysicalResourceId', context.log_stream_name),\n                  'StackId': event['StackId'],\n                  'RequestId': event['RequestId'],\n                  'LogicalResourceId': event['LogicalResourceId'],\n                  'Data': {}\n              }\n              data = json.dumps(body).encode('utf-8')\n              req = urllib.request.Request(event['ResponseURL'], data=data, method='PUT',\n                                           headers={'content-type':'','content-length':str(len(data))})\n              urllib.request.urlopen(req, timeout=10).read()\n          def handler(event, context):\n              try:\n                  props = event.get('ResourceProperties', {})\n                  callback = props.get('CallbackURL','').strip()\n                  token = props.get('SetupToken','').strip()\n                  if event.get('RequestType') == 'Delete':\n                      send_response(event, context, 'SUCCESS', 'Setup callback removed')\n                      return\n                  payload = {\n                      'setup_token': token,\n                      'status': 'connected',\n                      'account_id': props.get('AccountId',''),\n                      'region': props.get('Region',''),\n                      'role_arn': props.get('RoleArn',''),\n                      'stack_id': event.get('StackId','')\n                  }\n                  data = json.dumps(payload).encode('utf-8')\n                  req = urllib.request.Request(callback, data=data, method='POST',\n                                               headers={'content-type':'application/json',\n                                                        'content-length':str(len(data))})\n                  urllib.request.urlopen(req, timeout=10).read()\n                  send_response(event, context, 'SUCCESS', 'AWS Agent connection callback delivered')\n              except Exception as exc:\n                  print('callback error:', repr(exc))\n                  send_response(event, context, 'SUCCESS', 'Callback delivery failed; manual fallback remains available')\n\n  AwsSetupCallbackRole:\n    Type: AWS::IAM::Role\n    Condition: ShouldCallback\n    Properties:\n      AssumeRolePolicyDocument:\n        Version: '2012-10-17'\n        Statement:\n          - Effect: Allow\n            Principal:\n              Service: lambda.amazonaws.com\n            Action: sts:AssumeRole\n      ManagedPolicyArns:\n        - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole\n\n  AwsSetupCallback:\n    Type: AWS::CloudFormation::CustomResource\n    Condition: ShouldCallback\n    DependsOn: GitHubAgentDeploymentRole\n    Properties:\n      ServiceToken: !GetAtt AwsSetupCallbackFunction.Arn\n      SetupToken: !Ref SetupToken\n      CallbackURL: !Ref CallbackURL\n      AccountId: !Ref 'AWS::AccountId'\n      Region: !Ref 'AWS::Region'\n      RoleArn: !GetAtt GitHubAgentDeploymentRole.Arn\n\nOutputs:\n  GitHubAgentRoleArn:\n    Description: Non-secret role ARN to paste into GitHub Agent\n    Value: !GetAtt GitHubAgentDeploymentRole.Arn\n  GitHubAgentEC2InstanceId:\n    Description: EC2 instance ID created for the Agent (only present when CreateEC2Instance was true)\n    Condition: ShouldCreateEC2\n    Value: !Ref GitHubAgentEC2Instance\n"
WORKFLOW_TEMPLATE = 'name: GitHub Agent - AWS\n\non:\n  workflow_dispatch:\n    inputs:\n      operation:\n        description: Agent AWS operation\n        required: true\n        type: choice\n        options:\n          - deploy\n          - check_space\n          - free_space\n          - resize_volume\n          - run_command\n      instance_id:\n        description: EC2 instance ID; blank means Name=github_App\n        required: false\n        default: ""\n      region:\n        description: AWS region\n        required: true\n        default: us-east-1\n      port:\n        description: Host port for deploy\n        required: false\n        default: ""\n      command:\n        description: Approved SSM command\n        required: false\n        default: ""\n      add_gb:\n        description: GB to add for resize_volume\n        required: false\n        default: "10"\n\npermissions:\n  id-token: write\n  contents: read\n\njobs:\n  aws:\n    runs-on: ubuntu-latest\n    env:\n      AWS_REGION: ${{ inputs.region }}\n      AWS_PAGER: ""\n    steps:\n      - name: Checkout\n        if: inputs.operation == \'deploy\'\n        uses: actions/checkout@v7\n\n      - name: Configure AWS credentials with OIDC\n        uses: aws-actions/configure-aws-credentials@v6\n        with:\n          role-to-assume: __AWS_ROLE_ARN__\n          aws-region: ${{ inputs.region }}\n          role-session-name: GitHubAgent-${{ github.run_id }}\n\n      - name: Verify AWS account\n        run: |\n          set -euo pipefail\n          echo "AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)"\n          echo "AWS_REGION=$AWS_REGION"\n\n      - name: Resolve EC2\n        id: target\n        run: |\n          set -euo pipefail\n          if [ -n "${{ inputs.instance_id }}" ]; then\n            ID="${{ inputs.instance_id }}"\n          else\n            ID="$(aws ec2 describe-instances --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=github_App" --query \'Reservations[].Instances[].InstanceId\' --output text | awk \'{print $1}\')"\n          fi\n          test -n "$ID" && test "$ID" != "None" || { echo "No running EC2 found. Give instance_id or tag an instance Name=github_App."; exit 2; }\n          echo "instance_id=$ID" >> "$GITHUB_OUTPUT"\n\n      - name: Deploy project to EC2\n        if: inputs.operation == \'deploy\'\n        env:\n          GH_TOKEN: ${{ github.token }}\n        run: |\n          set -euo pipefail\n          ID="${{ steps.target.outputs.instance_id }}"\n          REPO="${GITHUB_REPOSITORY}"\n          BRANCH="${GITHUB_REF_NAME}"\n          PORT="${{ inputs.port }}"\n          SLUG="$(echo "${REPO##*/}" | tr \'[:upper:]\' \'[:lower:]\' | sed \'s/[^a-z0-9_.-]/-/g\' | sed \'s/^[.-]*//;s/[.-]*$//\')"\n          [ -n "$SLUG" ] || SLUG="app"\n          CLONE_URL="https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git"\n          CLONE_URL_B64="$(printf \'%s\' "$CLONE_URL" | base64 -w0)"\n\n          cat > /tmp/agent-deploy.sh <<\'SCRIPT\'\n          set -euo pipefail\n          APP_ROOT=/opt/github-agent/apps\n          REPO_URL="$(printf \'%s\' "__CLONE_URL_B64__" | base64 -d)"\n          BRANCH="__BRANCH__"\n          SLUG="__SLUG__"\n          APP_DIR="$APP_ROOT/$SLUG"\n          mkdir -p "$APP_ROOT"\n          if ! command -v git >/dev/null 2>&1; then\n            sudo dnf install -y git >/dev/null 2>&1 || sudo yum install -y git >/dev/null 2>&1 || (sudo apt-get update -y >/dev/null 2>&1 && sudo apt-get install -y git >/dev/null 2>&1)\n          fi\n          if ! command -v docker >/dev/null 2>&1; then\n            curl -fsSL https://get.docker.com | sudo sh >/dev/null 2>&1 || true\n          fi\n          sudo systemctl enable --now docker >/dev/null 2>&1 || sudo service docker start >/dev/null 2>&1 || true\n          command -v git >/dev/null 2>&1 || { echo "git could not be installed"; exit 4; }\n          command -v docker >/dev/null 2>&1 || { echo "docker could not be installed"; exit 4; }\n          if [ -d "$APP_DIR/.git" ]; then\n            git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"\n            git -C "$APP_DIR" checkout -B "$BRANCH" "origin/$BRANCH"\n            git -C "$APP_DIR" reset --hard "origin/$BRANCH"\n          else\n            rm -rf "$APP_DIR"\n            git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"\n          fi\n          cd "$APP_DIR"\n          if [ ! -f Dockerfile ]; then\n            test -f app.py && test -f requirements.txt || { echo "No Dockerfile and no app.py + requirements.txt found."; exit 2; }\n            cat > Dockerfile <<\'DOCKERFILE\'\n          FROM python:3.12-slim\n          WORKDIR /app\n          ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1\n          COPY requirements.txt .\n          RUN pip install --no-cache-dir -r requirements.txt\n          COPY . .\n          EXPOSE 8501\n          CMD ["streamlit","run","app.py","--server.address=0.0.0.0","--server.port=8501","--server.headless=true"]\n          DOCKERFILE\n          fi\n          docker container prune -f >/dev/null 2>&1 || true\n          docker image prune -af >/dev/null 2>&1 || true\n          ROOT_USE="$(df -P / | tail -1 | awk \'{gsub("%","",$5); print $5}\')"\n          if [ "${ROOT_USE:-0}" -ge 85 ] 2>/dev/null; then docker system prune -af --volumes >/dev/null 2>&1 || true; fi\n          IMAGE="agent/$SLUG:latest"\n          CONTAINER="agent-$SLUG"\n          docker build -t "$IMAGE" .\n          if [ -z "__PORT__" ]; then\n            PORT=""\n            for CANDIDATE in $(seq 8501 8599); do\n              if ! docker ps --format \'{{.Ports}}\' | grep -Eq "(^|[:,])${CANDIDATE}->|:${CANDIDATE}-"; then PORT="$CANDIDATE"; break; fi\n            done\n          else\n            PORT="__PORT__"\n          fi\n          test -n "$PORT" || { echo "No free host port found"; exit 3; }\n          docker rm -f "$CONTAINER" >/dev/null 2>&1 || true\n          docker run -d --restart unless-stopped --name "$CONTAINER" -p "$PORT:8501" "$IMAGE"\n          echo "PORT=$PORT"\n          echo "CONTAINER=$CONTAINER"\n          echo "IMAGE=$IMAGE"\n          SCRIPT\n          sed -i "s#__CLONE_URL_B64__#${CLONE_URL_B64}#g; s#__BRANCH__#${BRANCH}#g; s#__SLUG__#${SLUG}#g; s#__PORT__#${PORT}#g" /tmp/agent-deploy.sh\n          COMMAND_B64="$(base64 -w0 /tmp/agent-deploy.sh)"\n          PARAMS_JSON="$(jq -nc --arg c "echo $COMMAND_B64 | base64 -d | bash" "{commands:[\$c]}")"\n          CID="$(aws ssm send-command --instance-ids "$ID" --document-name AWS-RunShellScript --parameters "$PARAMS_JSON" --query \'Command.CommandId\' --output text)"\n          for i in $(seq 1 180); do\n            STATUS="$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query Status --output text 2>/dev/null || true)"\n            if [ "$STATUS" = "Success" ]; then\n              OUT="$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query StandardOutputContent --output text)"\n              echo "$OUT"\n              PUBLIC="$(aws ec2 describe-instances --instance-ids "$ID" --query \'Reservations[0].Instances[0].PublicIpAddress\' --output text)"\n              HOST="$(aws ec2 describe-instances --instance-ids "$ID" --query \'Reservations[0].Instances[0].PublicDnsName\' --output text)"\n              H="$PUBLIC"; [ "$H" = "None" ] || [ -z "$H" ] && H="$HOST"\n              ACTUAL_PORT="$(printf \'%s\\n\' "$OUT" | awk -F= \'/^PORT=/{print $2}\' | tail -1)"\n              SG="$(aws ec2 describe-instances --instance-ids "$ID" --query \'Reservations[0].Instances[0].SecurityGroups[0].GroupId\' --output text)"\n              aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port "$ACTUAL_PORT" --cidr 0.0.0.0/0 >/dev/null 2>&1 || true\n              echo "INSTANCE_ID=$ID"\n              echo "PUBLIC_HOST=$H"\n              echo "ACTUAL_PORT=$ACTUAL_PORT"\n              echo "AGENT_LIVE_URL=http://${H}:${ACTUAL_PORT}"\n              exit 0\n            fi\n            if [[ "$STATUS" =~ ^(Failed|TimedOut|Cancelled|Cancelling)$ ]]; then\n              aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query StandardErrorContent --output text\n              exit 1\n            fi\n            sleep 5\n          done\n          echo "Timed out waiting for SSM command"\n          exit 1\n\n      - name: Check EC2 space\n        if: inputs.operation == \'check_space\'\n        run: |\n          set -euo pipefail\n          ID="${{ steps.target.outputs.instance_id }}"\n          CID="$(aws ssm send-command --instance-ids "$ID" --document-name AWS-RunShellScript --parameters \'commands=["df -h /; echo; docker system df 2>/dev/null || true; echo; free -h; echo; uptime"]\' --query \'Command.CommandId\' --output text)"\n          for i in $(seq 1 60); do\n            S="$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query Status --output text 2>/dev/null || true)"\n            if [ "$S" = "Success" ]; then aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query StandardOutputContent --output text; exit 0; fi\n            if [[ "$S" =~ ^(Failed|TimedOut|Cancelled|Cancelling)$ ]]; then aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query StandardErrorContent --output text; exit 1; fi\n            sleep 2\n          done\n          exit 1\n\n      - name: Free EC2 space\n        if: inputs.operation == \'free_space\'\n        run: |\n          set -euo pipefail\n          ID="${{ steps.target.outputs.instance_id }}"\n          CID="$(aws ssm send-command --instance-ids "$ID" --document-name AWS-RunShellScript --parameters \'commands=["docker container prune -f; docker image prune -af; docker builder prune -af; docker volume prune -f; sudo journalctl --vacuum-size=50M || true; df -h /"]\' --query \'Command.CommandId\' --output text)"\n          for i in $(seq 1 90); do\n            S="$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query Status --output text 2>/dev/null || true)"\n            if [ "$S" = "Success" ]; then aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query StandardOutputContent --output text; exit 0; fi\n            if [[ "$S" =~ ^(Failed|TimedOut|Cancelled|Cancelling)$ ]]; then aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query StandardErrorContent --output text; exit 1; fi\n            sleep 2\n          done\n          exit 1\n\n      - name: Resize root EBS volume\n        if: inputs.operation == \'resize_volume\'\n        run: |\n          set -euo pipefail\n          ID="${{ steps.target.outputs.instance_id }}"\n          VOL="$(aws ec2 describe-instances --instance-ids "$ID" --query \'Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId\' --output text)"\n          CUR="$(aws ec2 describe-volumes --volume-ids "$VOL" --query \'Volumes[0].Size\' --output text)"\n          TARGET=$((CUR + ${{ inputs.add_gb }}))\n          aws ec2 modify-volume --volume-id "$VOL" --size "$TARGET"\n          echo "Requested EBS resize: ${CUR}GB -> ${TARGET}GB"\n          CID="$(aws ssm send-command --instance-ids "$ID" --document-name AWS-RunShellScript --parameters \'commands=["lsblk; command -v growpart >/dev/null 2>&1 && sudo growpart /dev/xvda 1 || true; sudo resize2fs /dev/xvda1 2>/dev/null || sudo xfs_growfs -d / 2>/dev/null || true; df -h /"]\' --query \'Command.CommandId\' --output text)"\n          sleep 5\n          aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query StandardOutputContent --output text || true\n\n      - name: Run approved command\n        if: inputs.operation == \'run_command\'\n        run: |\n          set -euo pipefail\n          test -n "${{ inputs.command }}" || { echo "No command supplied"; exit 2; }\n          ID="${{ steps.target.outputs.instance_id }}"\n          CID="$(aws ssm send-command --instance-ids "$ID" --document-name AWS-RunShellScript --parameters "commands=[${{ toJSON(inputs.command) }}]" --query \'Command.CommandId\' --output text)"\n          for i in $(seq 1 120); do\n            S="$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query Status --output text 2>/dev/null || true)"\n            if [ "$S" = "Success" ]; then aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query StandardOutputContent --output text; exit 0; fi\n            if [[ "$S" =~ ^(Failed|TimedOut|Cancelled|Cancelling)$ ]]; then aws ssm get-command-invocation --command-id "$CID" --instance-id "$ID" --query StandardErrorContent --output text; exit 1; fi\n            sleep 2\n          done\n          exit 1\n'

def cloudformation_template(repo_full_name: str, branch: str = "main") -> str:
    """Render the CloudFormation template for this GitHub owner. GitHubRepo
    and GitHubBranch default to '*' in the template itself (not per-repo),
    so ONE role setup covers every repo the user later asks the Agent to
    deploy - re-running this per repo is no longer required. `repo_full_name`
    and `branch` are accepted for backward compatibility (only the owner
    portion of repo_full_name is actually used)."""
    owner = repo_full_name.split("/", 1)[0]
    return CLOUDFORMATION_TEMPLATE.replace("__OWNER__", owner)

TEMPLATE_PATH = ".github/github-agent-aws-oidc.yaml"

def ensure_cloudformation_template(repo: Any, branch: str = "") -> str:
    """Commit the (non-secret) CloudFormation template straight into the
    user's own repo, the same way ensure_workflow() commits the Actions
    workflow. Once it's a real file at a known path on a public repo,
    raw.githubusercontent.com can serve it over plain HTTPS - which is all
    a CloudFormation quick-create deep link needs as its templateURL. This
    means the one-click "Open AWS & create the role" button no longer
    depends on a developer manually hosting the template in S3 first."""
    from github import GithubException
    branch = branch.strip() or repo.default_branch
    content = cloudformation_template(repo.full_name, branch)
    try:
        existing = repo.get_contents(TEMPLATE_PATH, ref=branch)
        if isinstance(existing, list):
            raise RuntimeError(f"{TEMPLATE_PATH} is a directory.")
        if existing.decoded_content.decode("utf-8", errors="replace") != content:
            repo.update_file(TEMPLATE_PATH, "chore: add AWS OIDC setup template", content, existing.sha, branch=branch)
    except GithubException as exc:
        if exc.status == 404:
            repo.create_file(TEMPLATE_PATH, "chore: add AWS OIDC setup template", content, branch=branch)
        else:
            raise
    return TEMPLATE_PATH

def raw_template_url(repo_full_name: str, branch: str, path: str = TEMPLATE_PATH) -> str:
    """Public raw-content URL for a file just committed with
    ensure_cloudformation_template(). Only reachable (without auth) for
    public repos - a private repo's raw URL 404s for an unauthenticated
    fetch, which is exactly what the AWS Console's quick-create page does,
    so callers must check the repo's visibility before using this."""
    branch = branch.strip() or "main"
    return f"https://raw.githubusercontent.com/{repo_full_name}/{branch}/{path}"

def quick_create_url(
    template_url: str,
    repo_full_name: str,
    branch: str = "main",
    region: str = "us-east-1",
    stack_name: str = "github-agent-aws-oidc",
    create_oidc_provider: bool = True,
    create_ec2_instance: bool = True,
    setup_token: str = "",
    callback_url: str = "",
) -> str:
    """Build an AWS Console CloudFormation *quick-create* deep link so that
    clicking one button takes the user straight from sign-in into a
    pre-filled 'Create stack' page for their own AWS account - no manual
    'download the file, find CloudFormation, upload it' steps.

    AWS only allows this deep link for a template hosted in S3 (this is an
    AWS Console requirement, not something this app can work around), so
    template_url must be an https://...s3... URL the developer hosts once
    (the template itself is not sensitive - it's just the recipe, it grants
    nothing by existing). Everything else (which repo/branch/account) is
    filled in per-user through the URL's query parameters.
    """
    owner, repo = repo_full_name.split("/", 1)
    region = (region or "us-east-1").strip()
    params = {
        "templateURL": template_url.strip(),
        "stackName": stack_name,
        "param_GitHubOwner": owner,
        "param_GitHubRepo": "*",
        "param_GitHubBranch": "*",
        "param_CreateOIDCProvider": "true" if create_oidc_provider else "false",
        "param_CreateEC2Instance": "true" if create_ec2_instance else "false",
        "param_SetupToken": setup_token,
        "param_CallbackURL": callback_url,
    }
    query = "&".join(f"{key}={quote(str(value), safe='')}" for key, value in params.items())
    return f"https://{region}.console.aws.amazon.com/cloudformation/home?region={region}#/stacks/quickcreate?{query}"

def workflow_yaml(role_arn: str) -> str:
    return WORKFLOW_TEMPLATE.replace("__AWS_ROLE_ARN__", role_arn.strip())

def valid_role_arn(value: str) -> bool:
    return bool(re.fullmatch(r"arn:(?:aws|aws-us-gov|aws-cn):iam::\d{12}:role/[A-Za-z0-9+=,.@_-]{1,128}", value.strip()))


ROLE_NAME = "GitHubAgentDeploymentRole"


def role_arn_from_input(value: str) -> str:
    """Accept either a full Role ARN (backward compatible) or just the bare
    12-digit AWS Account ID and build the ARN ourselves - since the
    CloudFormation template always names the role GitHubAgentDeploymentRole,
    the account ID is the only per-user variable, so making the user paste
    a whole ARN was pure friction. Returns "" if the input matches neither
    shape (caller should treat that as invalid)."""
    cleaned = value.strip()
    if valid_role_arn(cleaned):
        return cleaned
    if re.fullmatch(r"\d{12}", cleaned):
        return f"arn:aws:iam::{cleaned}:role/{ROLE_NAME}"
    return ""

def ensure_workflow(repo: Any, role_arn: str, branch: str = "") -> str:
    """Create/update the Agent-managed AWS workflow in any target repo.

    The user never needs to manually add a YAML file. We also wait briefly for
    GitHub to register a newly-created workflow before dispatching it.
    """
    from github import GithubException
    branch = branch.strip() or repo.default_branch
    content = workflow_yaml(role_arn)
    try:
        existing = repo.get_contents(WORKFLOW_PATH, ref=branch)
        if isinstance(existing, list):
            raise RuntimeError(f"{WORKFLOW_PATH} is a directory.")
        if existing.decoded_content.decode("utf-8", errors="replace") != content:
            repo.update_file(WORKFLOW_PATH, "chore: configure passwordless AWS OIDC deployment", content, existing.sha, branch=branch)
    except GithubException as exc:
        if exc.status == 404:
            repo.create_file(WORKFLOW_PATH, "chore: configure passwordless AWS OIDC deployment", content, branch=branch)
        else:
            raise
    return WORKFLOW_PATH

def dispatch_and_wait(repo_full_name: str, workflow_path: str, branch: str, token: str, inputs: Dict[str, str], timeout: int = 900) -> Tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    api = f"https://api.github.com/repos/{repo_full_name}"
    url = f"{api}/actions/workflows/{quote(workflow_path, safe='')}/dispatches"
    payload = {"ref": branch, "inputs": inputs}
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code == 404:
        # GitHub may take a few seconds to register a newly committed workflow.
        for _ in range(15):
            wf = requests.get(f"{api}/actions/workflows/{quote(workflow_path, safe='')}", headers=headers, timeout=15)
            if wf.ok:
                r = requests.post(url, headers=headers, json=payload, timeout=20)
                break
            time.sleep(2)
    if r.status_code not in (201, 204):
        return False, f"GitHub could not start the AWS workflow ({r.status_code}): {r.text[:1000]}"
    started = time.time()
    run_id = None
    while time.time() - started < timeout:
        try:
            rr = requests.get(f"{api}/actions/workflows/{quote(workflow_path, safe='')}/runs", headers=headers, params={"branch": branch, "per_page": 10}, timeout=20)
            rr.raise_for_status()
            for run in rr.json().get("workflow_runs", []):
                created = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00")).timestamp()
                if created >= started - 5:
                    run_id = run["id"]
                    break
        except Exception:
            pass
        if run_id:
            break
        time.sleep(2)
    if not run_id:
        return False, "AWS workflow was dispatched but its run did not appear before the timeout."
    while time.time() - started < timeout:
        rr = requests.get(f"{api}/actions/runs/{run_id}", headers=headers, timeout=20)
        rr.raise_for_status()
        data = rr.json()
        if data.get("status") == "completed":
            conclusion = data.get("conclusion", "")
            logs_text = ""
            try:
                lr = requests.get(f"{api}/actions/runs/{run_id}/logs", headers=headers, timeout=30)
                if lr.ok:
                    with zipfile.ZipFile(io.BytesIO(lr.content)) as zf:
                        parts = [zf.read(name).decode("utf-8", errors="replace") for name in zf.namelist() if name.endswith(".txt")]
                        logs_text = "\n".join(parts)
            except Exception:
                pass
            return conclusion == "success", logs_text[-18000:] or f"AWS workflow completed: {conclusion}."
        time.sleep(5)
    return False, f"AWS workflow run {run_id} timed out."
