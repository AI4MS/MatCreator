FROM python:3.12-slim AS python-base

RUN pip install --no-cache-dir uv -i https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

ENV UV_SYSTEM_PYTHON=1
RUN uv pip install . -i https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONUNBUFFERED=1


# Per-user execution runtime. This target deliberately excludes the web
# application, frontend bundle, and Node.js.
FROM python-base AS worker

EXPOSE 8000
CMD ["matcreator", "api-server", "--host", "0.0.0.0", "--port", "8000"]


# Compile the browser application once for the production control plane.
FROM node:24-bookworm-slim AS frontend-build

WORKDIR /app/web/vite-frontend

COPY web/vite-frontend/package*.json ./
RUN npm install

COPY web/vite-frontend/ ./
RUN npm run build


# Shared-server API and frontend runtime. Node.js is not present in this image.
FROM python-base AS control-plane

COPY web/main.py web/users_db.py web/
COPY --from=frontend-build /app/web/vite-frontend/dist web/vite-frontend/dist

EXPOSE 8001
CMD ["python", "web/main.py"]


# Preserve the existing all-in-one local Docker workflow as the default target.
FROM python-base AS local

RUN sed -i \
        -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
        -e 's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    curl bash \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY web/vite-frontend/package*.json web/vite-frontend/
RUN cd web/vite-frontend && npm install

COPY . .

# Build the production frontend bundle (served as static files by web/main.py).
RUN cd web/vite-frontend && npm run build

# Optional: bake the repo-local VASP POTCAR library into the image and configure pymatgen.
# Uncomment these lines only if your deployment is allowed to package POTCAR files in the image.
# COPY PATH_TO_POT /opt/vasp/POT_GGA_PAW_PBE
# ENV PMG_VASP_PSP_DIR=/opt/vasp/POT_GGA_PAW_PBE
# RUN pmg config --add PMG_VASP_PSP_DIR "$PMG_VASP_PSP_DIR"

# Run the bohrctl installation script if you need to submit jobs to a Bohr cluster. Uncomment the following line if you need it.
# RUN curl -fsSL https://dp-public.oss-cn-beijing.aliyuncs.com/bohrctl/1.0.0/install_bohr_linux_curl.sh | bash

# Optional: bake a curated skill directory into the image and use it as the
# default module skill root for local/server startup. Alternatively, mount a
# host skill directory at /app/custom-skills when starting the container.
# COPY PATH_TO_SELECTED_SKILLS /app/custom-skills
# ENV MATCREATOR_MODULE_SKILLS_ROOT=/app/custom-skills

EXPOSE 8000 8001 5173
CMD ["bash", "script/start_matcreator.sh"]
