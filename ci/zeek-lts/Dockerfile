FROM centos:7

RUN yum -y install \
    epel-release \
  && yum clean all && rm -rf /var/cache/yum

RUN yum -y install \
    centos-release-scl \
  && yum clean all && rm -rf /var/cache/yum

RUN yum -y install \
    devtoolset-7 \
  && yum clean all && rm -rf /var/cache/yum

RUN yum -y install \
    git \
    cmake \
    cmake3 \
    make \
    flex \
    bison \
    swig \
    openssl \
    openssl-devel \
    libpcap-devel \
    python3 \
    python3-devel \
    python3-pip\
    zlib-devel \
    sqlite \
    findutils \
    which \
    wget \
  && yum clean all && rm -rf /var/cache/yum

# Many distros adhere to PEP 394's recommendation for `python` = `python2` so
# this is a simple workaround until we drop Python 2 support and explicitly
# use `python3` for all invocations (e.g. in shebangs).
RUN ln -sf /usr/bin/python3 /usr/local/bin/python
RUN ln -sf /usr/bin/pip3 /usr/local/bin/pip

RUN pip install GitPython semantic_version btest

RUN echo 'unset BASH_ENV PROMPT_COMMAND ENV' > /usr/bin/zeek-ci-env && \
    echo 'source /opt/rh/devtoolset-7/enable' >> /usr/bin/zeek-ci-env

ENV BASH_ENV="/usr/bin/zeek-ci-env" \
    ENV="/usr/bin/zeek-ci-env" \
    PROMPT_COMMAND=". /usr/bin/zeek-ci-env" \
    PATH="${PATH}:/usr/local/zeek/bin"

ENV ZEEK_CI_VERSION 3.0.3

RUN cd /tmp && \
    source /opt/rh/devtoolset-7/enable && \
    wget https://download.zeek.org/zeek-${ZEEK_CI_VERSION}.tar.gz && \
    tar -xzf zeek-${ZEEK_CI_VERSION}.tar.gz && \
    rm zeek-${ZEEK_CI_VERSION}.tar.gz && \
    ( cd zeek-${ZEEK_CI_VERSION} && ./configure --build-type=release ) && \
    ( cd zeek-${ZEEK_CI_VERSION} && make -j $(nproc) install ) && \
    rm -rf zeek-${ZEEK_CI_VERSION}

RUN git config --global user.email "zeek@example.com" && \
    git config --global user.name "Zeek"
