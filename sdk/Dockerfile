ARG BASE_IMAGE=python:3.11-slim

FROM ${BASE_IMAGE} as builder
RUN pip install poetry
COPY . .
RUN poetry build

# Generate requirements.txt without local file references or editable installs
RUN poetry export --without dev --without-hashes --format=requirements.txt | sed '/^-e /d; /@ file/d' > dist/requirements.txt

FROM ${BASE_IMAGE} as sdk_base
COPY --from=builder dist/requirements.txt /tmp/eidolon_ai_sdk/requirements.txt
RUN pip install -r /tmp/eidolon_ai_sdk/requirements.txt
RUN playwright install
COPY --from=builder dist/*.whl /tmp/eidolon_ai_sdk/
RUN pip install /tmp/eidolon_ai_sdk/*.whl

FROM sdk_base as runner
RUN addgroup --system --gid 1001 eidolon
RUN adduser --system --uid 1001 eidolon
USER eidolon
EXPOSE 8080
ENV PYTHONUNBUFFERED 1
WORKDIR app
ENTRYPOINT ["eidolon-server"]
CMD ["resources"]