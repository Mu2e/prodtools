#!/bin/bash

echo "�� Starting All Parity Tests..."

echo "1. Stage1 Jobs..."
python parity_test.py --json "../Production/data/stage1.json"

echo "2. Resampler Jobs..."
python parity_test.py --json "../Production/data/resampler.json"

echo "3. Mixing Jobs..."
python parity_test.py --json "../Production/data/mix.json"

echo "4. Running comparison..."
./compare_tarballs.sh

echo "�� All tests completed!"
