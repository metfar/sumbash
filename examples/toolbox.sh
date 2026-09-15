#!/usr/bin/env sumbash
# Neutral smoke example for the first sumbash alpha.
TODAY=$(date +%Y%m%d)
echo "Today: $TODAY"
echo "5.5 * 2 = $((5.5 * 2))"
echo "sqrt(9) = $((sqrt(9)))"
echo "Architecture: $(suminfo --field machine.architecture)"
uptime -p
