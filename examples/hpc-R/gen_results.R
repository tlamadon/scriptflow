# R script for drawing a single sample from a bivariate normal.

# Get the arguments as a character vector.
args = commandArgs(trailingOnly=TRUE)
uid = args[1]
temp_dir = args[2]

# Pause momentarily and print job-id to the log-file
Sys.sleep(0.5)
print(uid)

# For illustrative purposes only: include error to trigger scriptflow's retry
if (runif(1) < 0.1) {
    this_throws_an_error
}#IF

# Generate the simulation draw
temp_res = c(rnorm(1, -1, 1), rnorm(1, 1, 1))

# Store results as .csv in the temp-directory
file_name <- paste0(temp_dir, "/res_", uid, ".RData")
write.csv(temp_res, file = file_name)