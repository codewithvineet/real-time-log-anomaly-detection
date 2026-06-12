pipeline {
    agent any

    /*
     * Environment variables available to all stages.
     * DOCKER_HUB_REPO: change this to your Docker Hub username.
     * IMAGE_TAG: uses the Git commit SHA so every build is uniquely tagged
     *            — you can always roll back to an exact commit.
     */
    environment {
        DOCKER_HUB_REPO  = "yourdockerhubusername"   // ← CHANGE THIS
        ML_IMAGE         = "${DOCKER_HUB_REPO}/ml-service"
        PRODUCER_IMAGE   = "${DOCKER_HUB_REPO}/log-producer"
        IMAGE_TAG        = "${env.GIT_COMMIT?.take(7) ?: 'latest'}"
        K8S_NAMESPACE    = "log-monitoring"
    }

    /*
     * Build triggers:
     *   - GitHub webhook fires on every push to main
     *   - Poll as fallback every 5 minutes in case the webhook misses
     */
    triggers {
        githubPush()
        pollSCM("H/5 * * * *")
    }

    options {
        // Keep only the last 10 builds to save disk space
        buildDiscarder(logRotator(numToKeepStr: "10"))
        // Fail the build if it takes longer than 30 minutes
        timeout(time: 30, unit: "MINUTES")
        // Don't run two builds of the same branch simultaneously
        disableConcurrentBuilds()
        // Add timestamps to every log line
        timestamps()
    }

    stages {

        // ── Stage 1: Checkout ──────────────────────────────────────────
        stage("Checkout") {
            steps {
                echo "📥 Checking out commit ${env.GIT_COMMIT} on branch ${env.GIT_BRANCH}"
                checkout scm
            }
        }

        // ── Stage 2: Test ──────────────────────────────────────────────
        // Tests run in a Python container — no need for Python on the Jenkins host.
        // If any test fails, the pipeline stops here and nothing gets built/deployed.
        stage("Test") {
            agent {
                docker {
                    image "python:3.10-slim"
                    // Reuse the same Docker daemon to avoid spinning up a new one
                    reuseNode true
                }
            }
            steps {
                echo "🧪 Installing test dependencies..."
                sh """
                    pip install --quiet -r tests/requirements-test.txt
                    pip install --quiet -r ml-service/requirements.txt
                    pip install --quiet -r log-producer/requirements.txt
                """

                echo "🧪 Running tests with coverage..."
                sh """
                    pytest tests/ \
                        --tb=short \
                        --junitxml=test-results.xml \
                        --cov=ml-service \
                        --cov=log-producer \
                        --cov-report=xml:coverage.xml \
                        --cov-report=term-missing
                """
            }
            post {
                always {
                    // Publish test results in Jenkins UI
                    junit "test-results.xml"
                    // Publish coverage report
                    publishCoverage adapters: [coberturaAdapter("coverage.xml")]
                }
                failure {
                    echo "❌ Tests failed. Stopping pipeline — no images will be built."
                }
            }
        }

        // ── Stage 3: Build Docker images ───────────────────────────────
        // Both images are built in parallel to save time.
        stage("Build") {
            parallel {
                stage("Build ml-service") {
                    steps {
                        echo "🔨 Building ${ML_IMAGE}:${IMAGE_TAG}"
                        sh """
                            docker build \
                                -t ${ML_IMAGE}:${IMAGE_TAG} \
                                -t ${ML_IMAGE}:latest \
                                ./ml-service
                        """
                    }
                }
                stage("Build log-producer") {
                    steps {
                        echo "🔨 Building ${PRODUCER_IMAGE}:${IMAGE_TAG}"
                        sh """
                            docker build \
                                -t ${PRODUCER_IMAGE}:${IMAGE_TAG} \
                                -t ${PRODUCER_IMAGE}:latest \
                                ./log-producer
                        """
                    }
                }
            }
        }

        // ── Stage 4: Push to Docker Hub ────────────────────────────────
        // Only runs on the main branch — feature branches build and test
        // but don't push images or deploy.
        stage("Push") {
            when {
                branch "main"
            }
            steps {
                echo "📤 Pushing images to Docker Hub..."
                withCredentials([usernamePassword(
                    credentialsId: "dockerhub-credentials",
                    usernameVariable: "DOCKER_USER",
                    passwordVariable: "DOCKER_PASS"
                )]) {
                    sh "echo ${DOCKER_PASS} | docker login -u ${DOCKER_USER} --password-stdin"

                    sh """
                        docker push ${ML_IMAGE}:${IMAGE_TAG}
                        docker push ${ML_IMAGE}:latest
                        docker push ${PRODUCER_IMAGE}:${IMAGE_TAG}
                        docker push ${PRODUCER_IMAGE}:latest
                    """

                    sh "docker logout"
                }
                echo "✅ Images pushed: ${ML_IMAGE}:${IMAGE_TAG}"
            }
        }

        // ── Stage 5: Deploy to Kubernetes ──────────────────────────────
        // Uses kubectl with the kubeconfig secret to talk to your cluster.
        // Updates the image tag in the running Deployment — K8s does
        // a rolling update (zero downtime) automatically.
        stage("Deploy") {
            when {
                branch "main"
            }
            steps {
                echo "🚀 Deploying to Kubernetes namespace: ${K8S_NAMESPACE}"
                withCredentials([file(
                    credentialsId: "kubeconfig",
                    variable: "KUBECONFIG"
                )]) {
                    // Apply any manifest changes first (new ConfigMaps, Services, etc.)
                    sh "kubectl apply -f k8s/ --namespace=${K8S_NAMESPACE}"

                    // Update the image tag in each Deployment to the new commit SHA.
                    // This triggers K8s rolling updates — new pods come up before old ones go down.
                    sh """
                        kubectl set image deployment/ml-service \
                            ml-service=${ML_IMAGE}:${IMAGE_TAG} \
                            --namespace=${K8S_NAMESPACE}

                        kubectl set image deployment/log-producer \
                            log-producer=${PRODUCER_IMAGE}:${IMAGE_TAG} \
                            --namespace=${K8S_NAMESPACE}
                    """

                    // Wait for the rollout to finish (or timeout after 3 minutes)
                    sh """
                        kubectl rollout status deployment/ml-service \
                            --namespace=${K8S_NAMESPACE} \
                            --timeout=180s

                        kubectl rollout status deployment/log-producer \
                            --namespace=${K8S_NAMESPACE} \
                            --timeout=180s
                    """

                    echo "✅ Rollout complete."
                }
            }
            post {
                failure {
                    // If deploy fails, roll back to the previous working version
                    withCredentials([file(credentialsId: "kubeconfig", variable: "KUBECONFIG")]) {
                        sh """
                            echo "⚠️ Deployment failed — rolling back..."
                            kubectl rollout undo deployment/ml-service --namespace=${K8S_NAMESPACE}
                            kubectl rollout undo deployment/log-producer --namespace=${K8S_NAMESPACE}
                        """
                    }
                }
            }
        }

        // ── Stage 6: Health Check ──────────────────────────────────────
        // After deploy, verify the new pods are actually healthy.
        // Polls the /health endpoint — if it doesn't respond within
        // 2 minutes, the build is marked as failed.
        stage("Health Check") {
            when {
                branch "main"
            }
            steps {
                echo "🩺 Verifying deployment health..."
                withCredentials([file(credentialsId: "kubeconfig", variable: "KUBECONFIG")]) {
                    sh """
                        # Get the NodePort assigned to ml-service
                        MINIKUBE_IP=\$(kubectl get node minikube \
                            -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')
                        ML_PORT=\$(kubectl get svc ml-service \
                            --namespace=${K8S_NAMESPACE} \
                            -o jsonpath='{.spec.ports[0].nodePort}')

                        ML_URL="http://\${MINIKUBE_IP}:\${ML_PORT}"
                        echo "Checking: \${ML_URL}/health"

                        # Poll until healthy or timeout (24 attempts × 5s = 2 minutes)
                        for i in \$(seq 1 24); do
                            STATUS=\$(curl -sf "\${ML_URL}/health" | python3 -c \
                                "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" \
                                2>/dev/null || echo "")

                            if [ "\$STATUS" = "healthy" ]; then
                                echo "✅ Health check passed on attempt \$i"
                                exit 0
                            fi

                            echo "Attempt \$i/24 — not ready yet, waiting 5s..."
                            sleep 5
                        done

                        echo "❌ Health check timed out after 2 minutes"
                        exit 1
                    """
                }
            }
        }
    }

    // ── Post-pipeline notifications ────────────────────────────────────
    post {
        success {
            echo """
            ╔══════════════════════════════════════════╗
            ║  ✅ Pipeline SUCCESS                     ║
            ║  Branch : ${env.GIT_BRANCH}              ║
            ║  Commit : ${IMAGE_TAG}                   ║
            ║  Images : pushed & deployed              ║
            ╚══════════════════════════════════════════╝
            """
        }
        failure {
            echo """
            ╔══════════════════════════════════════════╗
            ║  ❌ Pipeline FAILED                      ║
            ║  Branch : ${env.GIT_BRANCH}              ║
            ║  Commit : ${IMAGE_TAG}                   ║
            ║  Check logs above for details            ║
            ╚══════════════════════════════════════════╝
            """
        }
        always {
            // Clean up dangling Docker images to free disk space
            sh "docker image prune -f --filter 'dangling=true' || true"
            cleanWs()
        }
    }
}
