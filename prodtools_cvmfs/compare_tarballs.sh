#!/bin/bash
# Compare Python vs Perl tarballs with sorted JSON and show actual diff
# Also compare against existing dcache_tar files

echo "🔍 Comparing all generated configurations..."

# Get dcache_tar path using mdh print-url for each configuration

for py_tar in python/*.tar; do
    [ -f "$py_tar" ] || continue
    base=$(basename "$py_tar" .tar)
    perl_tar="perl/$base.tar"
    [ -f "$perl_tar" ] || continue

    echo "=== $base ==="
    
    # Compare Python vs Perl
    echo "Python vs Perl FCL identical?" && diff <(tar -xO -f "$py_tar" mu2e.fcl) <(tar -xO -f "$perl_tar" mu2e.fcl) && echo "✅" || echo "❌"
    
    echo "Python vs Perl JSON identical?"
    if diff <(tar -xO -f "$py_tar" jobpars.json | jq -S .) <(tar -xO -f "$perl_tar" jobpars.json | jq -S .); then
        echo "✅"
    else
        echo "❌"
        echo "  < = Python, > = Perl"
    fi
    
    # Try to get dcache_tar path using mdh print-url with disk location (use base filename)
    dcache_tar=$(mdh print-url -l disk "$base.tar" 2>/dev/null)
    
    # Compare against existing dcache_tar if it exists and has matching content
    if [ -n "$dcache_tar" ] && [ -f "$dcache_tar" ]; then
        echo "Python vs DCache_tar FCL identical?" && diff <(tar -xO -f "$py_tar" mu2e.fcl) <(tar -xO -f "$dcache_tar" mu2e.fcl) && echo "✅" || echo "❌"
        
        echo "Python vs DCache_tar JSON identical?"
        if diff <(tar -xO -f "$py_tar" jobpars.json | jq -S .) <(tar -xO -f "$dcache_tar" jobpars.json | jq -S .); then
            echo "✅"
        else
            echo "❌"
            echo "  < = Python, > = DCache_tar"
        fi
    else
        echo "⚠️  DCache_tar not found via mdh print-url for $py_tar"
    fi
    
    echo
done
