#! /usr/bin/env bash

result=0
cd testing
btest -d -b -x btest-results.xml -j ${ZEEK_CI_CPUS} || result=1
[[ -d .tmp ]] && tar -czf tmp.tar.gz .tmp
exit ${result}
