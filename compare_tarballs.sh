#!/bin/bash
# Compare Python vs Perl tarballs with sorted JSON and show actual diff
for py_tar in test/python/*.tar; do
    [ -f "$py_tar" ] || continue
    base=$(basename "$py_tar" .tar)
    perl_tar="test/perl/$base.tar"
    [ -f "$perl_tar" ] || continue
    
    echo "=== $base ==="
    echo "FCL identical?" && diff <(tar -xO -f "$py_tar" mu2e.fcl) <(tar -xO -f "$perl_tar" mu2e.fcl) && echo "✅" || echo "❌"
    
    echo "JSON identical?"
    if diff <(tar -xO -f "$py_tar" jobpars.json | jq -S .) <(tar -xO -f "$perl_tar" jobpars.json | jq -S .); then
        echo "✅"
    else
        echo "❌"
        echo "  < = Python, > = Perl"
    fi
    echo
done
