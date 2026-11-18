# Open-source readiness for issue #11

The public repository needs a concise README, a runnable first example,
contribution and security policies, and a reference release note. Apache-2.0
is already approved. Use the installed server and existing synthetic upstream
fixture for the example. Keep detailed configuration, operating limits and
transport guidance in a user guide. Include the policies in the source distribution.

Verify the example and installation in a clean environment outside the checkout.
Check real distribution contents and scan the tracked source and reachable Git
history before proposing the refactor for review. Verify the private reporting
channel with the owner. Release and hosting verification remain separate steps.

## Content and history review

Gitleaks 8.30.1, downloaded from its GitHub release and checksum-verified,
scanned all reachable commits and the staged candidate source. The default
rules found no matches. A supplemental WoS/OpenAlex assignment rule matched
two known synthetic fixture strings in each scan. The flagged lines were
checked against the test fixtures. No credential values were retained in the
project. The tree and historical path inventory contain no stored article
downloads or provider response data files. The integration test holds a
public DOI and retrieves content at runtime when explicitly enabled.

## Verification

258 offline tests passed on Python 3.12. The live integration test remained
disabled. Ruff, ty, actionlint and archive checks passed. Both built artifacts
passed installed stdio smoke checks. The README commands ran in a
fresh environment from the unpacked source archive. The CLI reported 0.1.0 and
the demo output matched the README JSON exactly. Local links in the source
distribution resolved, including heading anchors.

The repository owner enabled GitHub private vulnerability reporting. The
repository's GitHub settings endpoint confirmed that the channel is enabled.
