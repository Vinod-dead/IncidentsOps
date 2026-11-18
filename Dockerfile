FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY scenarios ./scenarios
COPY runbooks ./runbooks
COPY evals ./evals
RUN pip install --no-cache-dir -e .
ENV INCIDENTOPS_HOME=/data INCIDENTOPS_ROOT=/app
VOLUME /data
ENTRYPOINT ["incidentops"]
CMD ["--help"]
