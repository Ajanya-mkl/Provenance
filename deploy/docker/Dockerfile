FROM apache/airflow:2.9.3-python3.11

USER root
ARG AIRFLOW_CONSTRAINTS_URL=https://raw.githubusercontent.com/apache/airflow/constraints-2.9.3/constraints-3.11.txt

# Keep Airflow-side provider dependencies within the matching official constraints.
RUN pip install --no-cache-dir \
	"apache-airflow==2.9.3" \
	"apache-airflow-providers-postgres==5.13.0" \
	--constraint "${AIRFLOW_CONSTRAINTS_URL}" \
	&& mkdir -p /opt/venvs \
	&& chown airflow:root /opt/venvs

USER airflow
RUN python -m venv /opt/venvs/provenance
COPY --chown=airflow:root requirements.txt /tmp/provenance-requirements.txt

# These packages intentionally resolve separately from Airflow's constrained environment.
RUN /opt/venvs/provenance/bin/python -m pip install --no-cache-dir \
	-r /tmp/provenance-requirements.txt
