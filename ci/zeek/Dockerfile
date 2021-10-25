FROM centos:7

ARG zeek_package=zeek

RUN yum -y update ca-certificates \
  && yum clean all && rm -rf /var/cache/yum

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
    cmake \
    cmake3 \
    git \
    python3 \
    python3-pip\
    wget \
  && yum clean all && rm -rf /var/cache/yum

RUN cd /etc/yum.repos.d/ \
  && wget https://download.opensuse.org/repositories/security:zeek/CentOS_7/security:zeek.repo \
  && yum -y install $zeek_package \
  && yum clean all && rm -rf /var/cache/yum

RUN pip3 install GitPython semantic_version

RUN echo 'unset BASH_ENV PROMPT_COMMAND ENV' > /usr/bin/zeek-ci-env && \
    echo 'source /opt/rh/devtoolset-7/enable' >> /usr/bin/zeek-ci-env

ENV BASH_ENV="/usr/bin/zeek-ci-env" \
    ENV="/usr/bin/zeek-ci-env" \
    PROMPT_COMMAND=". /usr/bin/zeek-ci-env" \
    PATH="${PATH}:/opt/zeek/bin"

RUN git config --global user.email "zeek@example.com" && \
    git config --global user.name "Zeek"
