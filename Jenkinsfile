pipeline {
    agent any
    stages {
        stage('Build') {
            steps {
                sh 'docker build -t log-anomaly-ml ./ml-service'
            }
        }
        stage('Test') {
            steps {
                sh 'echo "Running tests (placeholder)"'
            }
        }
        stage('Deploy') {
            steps {
                sh 'kubectl apply -f k8s/'
            }
        }
    }
}