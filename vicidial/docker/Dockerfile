# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.11.16-alpine@sha256:6857d2dae63e052057f2db389a7061188ac9a92a3fa8d402bde68f36df6fada1

FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
COPY vicidial/docker/requirements.lock /build/requirements.lock
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --requirement /build/requirements.lock
COPY vicidial/pyproject.toml /build/vicidial/pyproject.toml
COPY vicidial/src /build/vicidial/src
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps /build/vicidial \
 && /opt/venv/bin/pip uninstall --yes pip setuptools wheel

FROM ${PYTHON_IMAGE} AS release
ARG SOURCE_SHA
ARG BUILD_DATE
ARG RELEASE_ENVIRONMENT=production-candidate
ARG OPENSSL_PACKAGE_VERSION=3.5.8-r0
ARG LIBUUID_PACKAGE_VERSION=2.42.3-r0
LABEL org.opencontainers.image.title="Codestra VICIdial Restricted Adapter" \
      org.opencontainers.image.revision="${SOURCE_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/appolon1908-hue/Vicidialer-Codestra"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODESTRA_RELEASE_METADATA_REQUIRED=true \
    CODESTRA_ADAPTER_STATE=/var/lib/codestra-vicidial/state.sqlite3 \
    CODESTRA_ADAPTER_DEPLOYMENT=production-candidate \
    ENABLE_VICIDIAL_WRITES=false \
    ENABLE_PROVISIONING=false \
    ENABLE_AGENT_SYNC=false \
    ENABLE_LEAD_PUBLICATION=false \
    ENABLE_LIVE_CALL_CONTROL=false \
    ENABLE_WARM_TRANSFER=false \
    ENABLE_IVR_ROUTING=false \
    ENABLE_IVR_CALLBACKS=false \
    ENABLE_PRODUCTION_DIALING=false \
    ENABLE_SMS_DELIVERY=false \
    ENABLE_EMAIL_DELIVERY=false \
    ENABLE_EXTERNAL_DELIVERY=false \
    ENABLE_AI_ACTIONS=false
COPY --from=builder /opt/venv /opt/venv
COPY vicidial /tmp/release-src/vicidial
COPY deploy/vicidial-restricted-adapter /tmp/release-src/deploy/vicidial-restricted-adapter
COPY .github/workflows/vicidial-adapter-container.yml /tmp/release-src/.github/workflows/vicidial-adapter-container.yml
COPY vicidial/docker/entrypoint.sh /usr/local/bin/codestra-vicidial-entrypoint
COPY vicidial/docker/security_regression.py /usr/local/libexec/codestra-security-regression
RUN apk add --no-cache \
      --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main \
      "libcrypto3=${OPENSSL_PACKAGE_VERSION}" \
      "libssl3=${OPENSSL_PACKAGE_VERSION}" \
      "libuuid=${LIBUUID_PACKAGE_VERSION}" \
 && test -n "$SOURCE_SHA" \
 && test -n "$BUILD_DATE" \
 && mkdir -p "/opt/codestra/releases/$SOURCE_SHA" /var/lib/codestra-vicidial \
 && printf '%s\n' "$SOURCE_SHA" > "/opt/codestra/releases/$SOURCE_SHA/SOURCE.COMMIT" \
 && SOURCE_SHA="$SOURCE_SHA" BUILD_DATE="$BUILD_DATE" RELEASE_ENVIRONMENT="$RELEASE_ENVIRONMENT" python -c 'import json,os; from codestra_vicidial.release import build_release_document; p="/opt/codestra/releases/"+os.environ["SOURCE_SHA"]+"/BUILD_INFO.json"; open(p,"w").write(json.dumps(build_release_document(git_sha=os.environ["SOURCE_SHA"],built_at=os.environ["BUILD_DATE"],environment=os.environ["RELEASE_ENVIRONMENT"]),sort_keys=True)+"\n")' \
 && python /tmp/release-src/vicidial/scripts/generate_release_manifest.py --root /tmp/release-src --source-sha "$SOURCE_SHA" --generated-at "$BUILD_DATE" --output "/opt/codestra/releases/$SOURCE_SHA/RELEASE_MANIFEST.json" \
 && python /tmp/release-src/vicidial/scripts/verify_release_manifest.py --root /tmp/release-src --manifest "/opt/codestra/releases/$SOURCE_SHA/RELEASE_MANIFEST.json" \
 && chmod 0555 /usr/local/bin/codestra-vicidial-entrypoint \
      /usr/local/libexec/codestra-security-regression \
 && python /usr/local/libexec/codestra-security-regression \
 && touch /var/lib/codestra-vicidial/.volume-owner \
 && chown -R 10001:10001 /var/lib/codestra-vicidial "/opt/codestra/releases/$SOURCE_SHA" \
 && rm -rf /tmp/release-src \
    /usr/local/lib/python3.11/site-packages/pip* \
    /usr/local/lib/python3.11/site-packages/setuptools* \
    /usr/local/lib/python3.11/ensurepip \
    /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11
ENV SOURCE_SHA=${SOURCE_SHA} \
    CODESTRA_BUILD_INFO_FILE=/opt/codestra/releases/${SOURCE_SHA}/BUILD_INFO.json \
    CODESTRA_RELEASE_ROOT=/opt/codestra/releases/${SOURCE_SHA}
USER 10001:10001
WORKDIR /opt/codestra
EXPOSE 8097
VOLUME ["/var/lib/codestra-vicidial"]
ENTRYPOINT ["/usr/local/bin/codestra-vicidial-entrypoint"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-c", "import json,os,urllib.request; b='http://127.0.0.1:8097'; assert json.load(urllib.request.urlopen(b+'/health',timeout=3))['status']=='live'; assert json.load(urllib.request.urlopen(b+'/ready',timeout=3))['status']=='ready'; assert json.load(urllib.request.urlopen(b+'/version',timeout=3))['git_sha']==os.environ['SOURCE_SHA']"]
CMD ["python", "-m", "uvicorn", "codestra_vicidial.production_app:app", "--host", "0.0.0.0", "--port", "8097", "--no-server-header"]
