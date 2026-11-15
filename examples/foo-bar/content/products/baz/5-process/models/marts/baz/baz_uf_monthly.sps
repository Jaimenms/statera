* LEGACY SPSS script (illustrative): mean value of records by UF and reference month.
* Adapted to run from the repository: only the file paths became FILE HANDLEs, which the
* framework defines (records = input; result = output). The rest is the original syntax.
*   BR-BAZ-001  mean value = total value / number of records, by UF and reference month.

GET FILE=records.
AGGREGATE OUTFILE=* /BREAK=period uf_id /records=N /total_value=SUM(value).
COMPUTE mean_value = total_value / records.
FORMATS records (F8.0) total_value mean_value (F12.2).
SORT CASES BY period uf_id.
SAVE OUTFILE=result.
