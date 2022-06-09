# R script for aggregating simulation results.

# Get the arguments as a character vector
args = commandArgs(trailingOnly=TRUE)
temp_dir = args[1]

# Get a list of all files in the temp-directory and sum cumulatively
filenames <- list.files(path = temp_dir, full.names = TRUE)
final_res <- c(0, 0)
for (file in filenames) {
    temp_res = read.csv(file)
    final_res = final_res + temp_res 
}#FOR

# Average and store final result
final_res = final_res / length(filenames)
write.csv(final_res, file = "results.csv")