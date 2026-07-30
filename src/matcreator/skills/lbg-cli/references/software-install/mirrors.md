# Domestic mirrors for package installs

Source: <https://docs.bohrium.com/docs/software/OtherSoftwares>

Default upstream package indexes often time out from inside the
platform. Use a domestic mirror instead.

## pip — Tsinghua mirror

One-off install:

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple some-package
```

The `-i` flag overrides the index for a single command. To make the
mirror persistent, configure it once via `pip config` (or write the
equivalent entry to `~/.pip/pip.conf`); subsequent `pip install` calls
will use it without `-i`.

## apt (Ubuntu) — Aliyun mirror

Replace the default `archive.ubuntu.com` source with the Aliyun
mirror. As a Dockerfile step:

```Dockerfile
RUN sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list
```

The same `sed` works as a plain shell command — drop the leading `RUN`:

```bash
sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list
```

Run `apt-get update` afterwards to refresh the package index against
the new source.

## yum (CentOS) — Aliyun mirror

The Bohrium docs note that `yum` should use a domestic mirror but do
not include a verbatim command. Follow the Aliyun CentOS mirror
documentation linked from the OtherSoftwares page above for the
current configuration steps for your CentOS major version.

## conda — Tsinghua Anaconda mirror

The Bohrium docs note that `conda` should use a domestic mirror but
do not include a verbatim command. Follow the Tsinghua Anaconda
mirror help page (linked from the OtherSoftwares page above) for the
current `~/.condarc` configuration.
