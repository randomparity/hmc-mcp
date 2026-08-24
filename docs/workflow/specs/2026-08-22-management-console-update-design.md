# Management-console update correction

Issue #398 requires the public console update tool to match IBM's documented Power10 and
Power11 REST contract. `hmc_update_console_software` accepts a console-specific source,
builds `UpdateManagementConsole` XML, and PUTs it to the matching operation path. Every
documented source field is passed through as a job parameter and unknown fields fail fast.

The public source keys are `MediaType`, `ServerHostOrIP`, `UserName`, `Password`,
`SFTPKey`, `PassPhrase`, `Directory`, `UpdateFile`, `MountLocation`, `MountOptions`,
`PTFNumber`, `Device`, and `RestartConsole`. `MediaType` is required because it selects
the update source and is one of `USB`, `NFS`, `SFTP`, `FTP`, `IBMWebsite`, `Disk`,
`VirtualMedia`, or `CDDVD`; the remaining string fields are optional because IBM documents
their meaning but not a universal requirement. `RestartConsole` is `True` or `False`.
This change does not invent media-specific conditional validation.

`kind="upgrade"` is retained only as a compatibility refusal: it raises before client
creation and explains that HMC upgrades require the documented sequence beginning with
`SaveUpgradeData`, rather than inventing a single operation. Waiting behavior is unchanged.

Acceptance is demonstrated by XML and mocked-request tests, rendered tool-schema tests,
and comparison with the Power10 `105-updatemanagementconsole_managementconsole-job.md`
and Power11 `121-updatemanagementconsole_managementconsole-job.md` snapshots supplied for
this campaign. Live-HMC validation is optional
and must be reported as not run when credentials or a suitable update target are absent.
