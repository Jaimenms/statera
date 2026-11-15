# LEGACY R script (illustrative): median value of records by UF and reference month.
# Adapted to run from the repository: only the file paths became the variables that the
# framework defines at the start (inputs$records = input; output = output file). Base R only.
#   BR-BAZ-002  median value by UF and reference month

records <- readRDS(inputs$records)
result <- aggregate(value ~ period + uf_id, data = records, FUN = median)
names(result)[names(result) == "value"] <- "median_value"
result <- result[order(result$period, result$uf_id), ]
saveRDS(result, output)
