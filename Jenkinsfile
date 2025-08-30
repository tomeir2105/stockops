pipeline {
  agent any

  environment {
    REGISTRY_USER = credentials('dockerhub-username')
    REGISTRY_PASS = credentials('dockerhub-password')
    DOCKERHUB_REPO = 'meir25'
    IMAGE_FETCHER = "${DOCKERHUB_REPO}/lse-fetcher"
    IMAGE_NEWS    = "${DOCKERHUB_REPO}/lse-news"
    KUBECONFIG = credentials('kubeconfig-file')
  }

  options { timestamps(); ansiColor('xterm') }

  parameters {
    string(name: 'RELEASE_TAG', defaultValue: 'v0.1.0', description: 'Semantic version (e.g., v0.1.0)')
    booleanParam(name: 'DEPLOY_TO_K3S', defaultValue: false, description: 'Deploy after build & tests')
    choice(name: 'ENV', choices: ['dev', 'prod'], description: 'Deployment env')
  }

  stages {
    stage('Checkout') {
      steps {
        checkout scm
        sh 'echo "Commit: $(git rev-parse --short HEAD)"'
      }
    }

    stage('Set Version') {
      steps {
        sh '''
          if [ -f VERSION ]; then cat VERSION; else echo "${RELEASE_TAG#v}" > VERSION; fi
          echo "Using version: $(cat VERSION)"
        '''
      }
    }

    stage('Lint & Validate') {
      steps {
        sh '''
          python -m pip install --user flake8
          python -m pip install --user yamllint yq || true
          echo "Linting python..."
          flake8 fetcher || true; flake8 news || true
          echo "Linting YAML..."
          yamllint -s helm || true
          echo "Helm lint..."
          helm repo add grafana https://grafana.github.io/helm-charts
          helm repo add influxdata https://helm.influxdata.com/
          helm dependency update helm/lse-stack || true
          helm lint helm/lse-stack
        '''
      }
    }

    stage('Build Images') {
      steps {
        sh '''
          VERSION="$(cat VERSION)"
          echo "$REGISTRY_PASS" | docker login -u "$REGISTRY_USER" --password-stdin
          docker build -t $IMAGE_FETCHER:$VERSION fetcher
          docker build -t $IMAGE_NEWS:$VERSION news
        '''
      }
    }

    stage('Unit Tests') {
      steps {
        sh '''
          python -m pip install --user -r fetcher/requirements.txt -r news/requirements.txt pytest
          pytest -q
        '''
      }
    }

    stage('Push Images') {
      steps {
        sh '''
          VERSION="$(cat VERSION)"
          docker push $IMAGE_FETCHER:$VERSION
          docker push $IMAGE_NEWS:$VERSION
        '''
      }
    }

    stage('Package Helm') {
      steps {
        sh '''
          VERSION="$(cat VERSION)"
          yq -i '.appVersion = strenv(VERSION)' helm/lse-stack/Chart.yaml || true
          helm dependency update helm/lse-stack || true
          helm template lse helm/lse-stack --values helm/lse-stack/values.yaml --set image.tag=$VERSION >/dev/null
        '''
      }
    }

    stage('Deploy to k3s') {
      when { expression { return params.DEPLOY_TO_K3S } }
      steps {
        sh '''
          export KUBECONFIG="$KUBECONFIG"
          kubectl create namespace lse --dry-run=client -o yaml | kubectl apply -f -
          VERSION="$(cat VERSION)"
          helm upgrade --install lse helm/lse-stack             --namespace lse             --set image.tag=$VERSION             --set image.fetcher.repository=$IMAGE_FETCHER             --set image.news.repository=$IMAGE_NEWS             -f helm/lse-stack/values.yaml
        '''
      }
    }
  }
}
