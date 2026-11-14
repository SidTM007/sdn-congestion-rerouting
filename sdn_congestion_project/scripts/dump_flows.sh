#!/usr/bin/env bash
# Show the flow tables of all four switches (use before and after a reroute).
for s in s1 s2 s3 s4; do
  echo "== $s =="
  sudo ovs-ofctl -O OpenFlow13 dump-flows "$s" | grep -v "priority=0"
done
