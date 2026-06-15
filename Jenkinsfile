pipeline {
    agent any

    /*
     * FIX for Problem 7: No triggers block at all.
     * An empty triggers {} block is invalid Groovy and crashes Jenkins.
     * For local/minikube setups, just trigger builds manually or
     * add a valid trigger only if you have a public GitHub webhook URL.
     *
     * If you have a public URL (e.g. via ngrok), uncomment this:
     *
     * triggers {
     *     githubPush()
     * }
     */

    options {
        buildDiscarder(logRotator(numToKeepStr: "10"))
        timeout(time: 30, unit: "MINUTES")
        disableConcurrentBuilds()
        timestamps()
    }

    /*
     * FIX for Problem 8: Do NOT put dynamic expressions in environment {}.
     * Jenkins evaluates environment {} at parse time, before any git info
     * is available. So GIT_COMMIT doesn't exist yet.
     * We set IMAGE_TAG inside a script {} block in the first stage instead.
     *
     * Only put STATIC strings here.
     */
    environment {
        DOCKER_HUB_REPO = "codewithvineet"
        ML_IMAGE         = "${DOCKER_HUB_REPO}/ml-service"
        PRODUCER_IMAGE   = "${DOCKER_HUB_REPO}/log-producer"
        K8S_NAMESPACE    = "log-monitoring"
        GITHUB_REPO = "https://github.com/codewithvineet/real-time-log-anomaly-detection.git"
    }

    stages {

        // ── Stage 1: Checkout ──────────────────────────────────────────
        // FIX for Problem 6: We do checkout manually here instead of
        // relying on Pipeline-from-SCM which broke due to root:root ownership.
        stage("Checkout") {
            steps {
                // Clean workspace before checkout
                deleteDir()

                git(
                    branch: 'main',
                    credentialsId: 'github-https',
                    url: GITHUB_REPO
                )

                /*
                 * FIX for Problem 8: Set IMAGE_TAG here, AFTER git checkout,
                 * when GIT_COMMIT is actually available.
                 * We store it as env.IMAGE_TAG so all later stages can use it.
                 */
                script {
                    env.IMAGE_TAG = sh(
                        script: "git rev-parse --short HEAD",
                        returnStdout: true
                    ).trim()

                    echo "Building commit: ${env.IMAGE_TAG}"
                }
            }
        }

        // ── Stage 2: Test ──────────────────────────────────────────────
        // Runs pytest with coverage. If any test fails, pipeline stops here.
        // Nothing gets built or deployed with a broken test suite.
        stage("Test") {
            steps {
                echo "Installing test dependencies..."
                sh """
                    python3 -m venv .venv
                    .venv/bin/python -m pip install --upgrade pip --quiet
                    .venv/bin/python -m pip install -r tests/requirements-test.txt --quiet
                    .venv/bin/python -m pip install -r ml-service/requirements.txt --quiet
                    .venv/bin/python -m pip install -r log-producer/requirements.txt --quiet
                """

                echo "Running tests..."
                sh """
                    .venv/bin/python -m pytest tests/ \
                        -v \
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
                    // Publish results in Jenkins UI even if tests fail
                    junit allowEmptyResults: true, testResults: "test-results.xml"
                }
                success {
                    echo "✅ All tests passed."
                }
                failure {
                    echo "❌ Tests failed. Pipeline stopping — no image will be built."
                }
            }
        }

        // ── Stage 3: Build Docker images ───────────────────────────────
        // Both images built in parallel to save time.
        // Tagged with both the short commit SHA and 'latest'.
        stage("Build") {
            parallel {
                stage("Build ml-service") {
                    steps {
                        echo "Building ${ML_IMAGE}:${env.IMAGE_TAG}"
                        sh """
                            docker build \
                                -t ${ML_IMAGE}:${env.IMAGE_TAG} \
                                -t ${ML_IMAGE}:latest \
                                ./ml-service
                        """
                    }
                }
                stage("Build log-producer") {
                    steps {
                        echo "Building ${PRODUCER_IMAGE}:${env.IMAGE_TAG}"
                        sh """
                            docker build \
                                -t ${PRODUCER_IMAGE}:${env.IMAGE_TAG} \
                                -t ${PRODUCER_IMAGE}:latest \
                                ./log-producer
                        """
                    }
                }
            }
        }

        // ── Stage 4: Push to Docker Hub ────────────────────────────────
        stage("Push") {
            steps {
                echo "Pushing images to Docker Hub..."
                withCredentials([usernamePassword(
                    credentialsId: "dockerhub-credentials",
                    usernameVariable: "DOCKER_USER",
                    passwordVariable: "DOCKER_PASS"
                )]) {
                    sh """
                        echo "${DOCKER_PASS}" | docker login -u "${DOCKER_USER}" --password-stdin

                        docker push ${ML_IMAGE}:${env.IMAGE_TAG}
                        docker push ${ML_IMAGE}:latest

                        docker push ${PRODUCER_IMAGE}:${env.IMAGE_TAG}
                        docker push ${PRODUCER_IMAGE}:latest

                        docker logout
                    """
                }
                echo "✅ Pushed: ${ML_IMAGE}:${env.IMAGE_TAG}"
            }
        }

        // ── Stage 5: Deploy to Kubernetes ──────────────────────────────
        stage("Deploy") {
            steps {
                echo "Deploying to Kubernetes..."
                withCredentials([file(
                    credentialsId: "kubeconfig",
                    variable: "KUBECONFIG"
                )]) {
                    // Apply all manifest changes first
                    sh "kubectl apply -f k8s/ --namespace=${K8S_NAMESPACE} --validate=false"

                    // Update image tags — this triggers a rolling update
                    sh """
                        kubectl set image deployment/ml-service \
                            ml-service=${ML_IMAGE}:${env.IMAGE_TAG} \
                            --namespace=${K8S_NAMESPACE}

                        kubectl set image deployment/log-producer \
                            log-producer=${PRODUCER_IMAGE}:${env.IMAGE_TAG} \
                            --namespace=${K8S_NAMESPACE}
                    """

                    // Wait for both rollouts to complete
                    sh """
                        kubectl rollout status deployment/ml-service \
                            --namespace=${K8S_NAMESPACE} --timeout=300s

                        kubectl rollout status deployment/log-producer \
                            --namespace=${K8S_NAMESPACE} --timeout=300s
                    """
                }
                echo "✅ Deployment complete."
            }
            post {
                failure {
                    echo "⚠️ Deploy failed — rolling back to previous version..."
                    withCredentials([file(credentialsId: "kubeconfig", variable: "KUBECONFIG")]) {
                        sh """
                            kubectl rollout undo deployment/ml-service --namespace=${K8S_NAMESPACE}
                            kubectl rollout undo deployment/log-producer --namespace=${K8S_NAMESPACE}
                        """
                    }
                }
            }
        }

        // ── Stage 6: Health Check ──────────────────────────────────────
        stage("Health Check") {
            steps {
                echo "Verifying deployment health..."
                withCredentials([file(credentialsId: "kubeconfig", variable: "KUBECONFIG")]) {
                    sh """
                        MINIKUBE_IP=\$(kubectl get node minikube \
                            -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')

                        ML_PORT=\$(kubectl get svc ml-service \
                            --namespace=${K8S_NAMESPACE} \
                            -o jsonpath='{.spec.ports[0].nodePort}')

                        ML_URL="http://\${MINIKUBE_IP}:\${ML_PORT}"
                        echo "Health check URL: \${ML_URL}/health"

                        for i in \$(seq 1 24); do
                            HTTP_CODE=\$(curl -s -o /dev/null -w "%{http_code}" "\${ML_URL}/health" || echo "000")

                            if [ "\$HTTP_CODE" = "200" ]; then
                                echo "✅ Health check passed on attempt \$i"
                                exit 0
                            fi

                            echo "Attempt \$i/24 — got HTTP \$HTTP_CODE, waiting 5s..."
                            sleep 5
                        done

                        echo "❌ Health check timed out"
                        exit 1
                    """
                }
            }
        }
    }

    /*
     * FIX for Problem 9: Use env.IMAGE_TAG (not bare ${IMAGE_TAG}) in post block.
     * The post block runs outside of any stage scope, so pipeline-level
     * variables set in environment {} are fine, but dynamic ones set
     * via script {} must be accessed as env.VARNAME.
     */
    post {
        success {
            echo "✅ Pipeline SUCCESS | commit=${env.IMAGE_TAG} | branch=${env.GIT_BRANCH}"
        }
        failure {
            echo "❌ Pipeline FAILED | commit=${env.IMAGE_TAG} | branch=${env.GIT_BRANCH}"
        }
        always {
            sh "docker image prune -f --filter 'dangling=true' || true"
            cleanWs()
        }
    }
}