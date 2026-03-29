# Calibration Validation States

Draft:

- raw measurement bundle exists
- fitted patch exists
- no benchmark confirmation yet

Validated:

- measurement bundle exists
- fitted patch is merged into an instrument profile
- at least one benchmark case confirms no obvious regression on the updated profile

Stale:

- firmware changed materially
- mechanical setup changed
- instrument profile changed outside the measured provenance
- benchmark corpus or simulator assumptions changed enough that earlier fit confidence is no longer trustworthy
