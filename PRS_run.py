
from __future__ import division
import pyspark
from pyspark.sql import SparkSession
from pyspark.sql import Row
from pyspark.sql.functions import udf
from pyspark.sql.types import *
from pyspark import SparkConf, SparkContext


from operator import add
import re
import glob, os
import csv
from collections import Counter
import ntpath
import functools

from math import log
import itertools
import PRS_utils as PRS_VCF_utils
from time import time
import sys
import argparse





# ATTN: python index starts at 0, so if you want to specify the second column, use 1


# define column number for contents in GWAS

parser = argparse.ArgumentParser(description='PRS Script Parameters')
# Mandatory positional arguments
parser.add_argument("GENO", action="store", help="Name of the Genotype files, can be a name or path, or name patterns with '*'")
parser.add_argument("GWAS", action="store", help="Name of the GWAS file, can be a name or path.")
parser.add_argument("Output", action="store", help="The path and name for the output file")

# Optional arguments

parser.add_argument("--gwas_id", action="store", default=0, dest="gwas_id",type=int, help="Column number in your GWAS that contains SNP ID, with first column being 0, default is 0")
parser.add_argument("--gwas_p", action="store", default=7, dest="gwas_p", type=int, help="Column number in your GWAS that contains p-value, with first column being 0, default is 7")
parser.add_argument("--gwas_or", action="store", default=5, dest="gwas_or", type=int, help="Column number in your GWAS that contains odds-ratio/beta, with first column being 0, default is 5")
parser.add_argument("--gwas_a1", action="store", default=3, dest="gwas_a1", type=int, help="Column number in your GWAS that contains allele A1, with first column being 0, default is 3. Allele A2 is assumed to be at column [gwas_a1+1]")
parser.add_argument("--gwas_maf", action="store", default=10, dest="gwas_maf", type=int, help="Column number in your GWAS that contains frequency of A1, with first column being 0, default is 10.")

#parser.add_argument("--geno_id", action="store", default=2, dest="geno_id",type=int, help="Column number in your genotype files that contains SNP ID, with first column being 0, default is 2")
#parser.add_argument("--geno_start", action="store", default=9,dest="geno_start", type=int, help="Column number in your genotype files that contains the first genotype,  with first column being 0, default is 9.")
#parser.add_argument("--geno_a1", action="store", default=10, dest="geno_a1",type=int, help="Column number in your genotype files that contains SNP ID, with first column being 0, default is 10.")
#parser.add_argument("--GENO_delim", action="store", default="\t", dest="GENO_delim", help="Delimtier of the GWAS file, default is tab-delimiter ")


parser.add_argument("--filetype", action="store",default="VCF", dest="filetype", help="The type of genotype file used as inputm choose between VCF and GEN, default is VCF", choices=set(["VCF", "GEN"]))

parser.add_argument("--thresholds", action="store", default=[0.5, 0.2, 0.1, 0.05, 0.01, 0.001, 0.0001], dest="thresholds", help="The p-value thresholds that controls which SNPs are used from the GWAS. Specifying the p-values simply by input one after another. default is [0.5, 0.2, 0.1, 0.05, 0.01, 0.001, 0.0001]", nargs="+", type=float)

parser.add_argument("--GWAS_delim", action="store", default="\t", dest="GWAS_delim", help="Delimtier of the GWAS file, default is tab-delimiter ")



parser.add_argument("--GWAS_no_header", action="store_false", default=True, dest="GWAS_header", help="Adding this parameter signals that there is no headers for the GWAS. The default is to assume that GWAS has column names")

parser.add_argument("--log_or", action="store_true", default=False, dest="log_or", help="Adding this parameter tells the script to log the effect sizes provided in the GWAS")

parser.add_argument("--check_ref", action="store_true", default=False, dest="check_ref", help="Adding this option tells the script to theck reference allele when determining genoypte calls. Default is not checking")

parser.add_argument("--app_name", action="store", default="PRS", dest="app_name", help="Give your spark application a name. Default is PRS.")

parser.add_argument("--sample_file", action="store", dest="sample_file", default="NOSAMPLE",help="path and name of the file that contain the sample labels. It is assumed that the sample labels are already in the same order as in the genotype file.")

parser.add_argument("--sample_delim", action="store", default=",", dest="sample_delim", help="Delimiter of the sample file. Default is comma")

parser.add_argument("--sample_file_ID", action="store", default=[0], type=int, nargs="+", dest="sample_file_ID", help="Specify which columns in the sample file are used as labels. Can use one integer to specify one column, or multiple integers to specify multiple columns. Default is the first column")

parser.add_argument("--sample_file_skip", action="store",default=1, dest="sample_skip", help="Specify how many lines to skip in the sample file, i.e. which row do the labels start. Default is 1, which assumes that the sample files has column names and the labels start on the second line")

parser.add_argument("--use_maf", action="store_true", default=False, dest="use_maf", help="Use this paramter to tell the script to calculate MAF in the provided propulation and compare it with MAF in the GWAS, in order to check the reference alleles of ambiguous SNPs (those whose A1 and A2 are reverese complements).  Not using this will result in ambiguous SNPs be discarded. Default is not using MAF")

parser.add_argument("--log", action="store", default=None, dest="log", help="Specify the location of the log file. Default is no log file")


results=parser.parse_args()

# type of files, VCF or GEN
filetype=results.filetype

## Setting parameters
gwas_id=results.gwas_id    # column of SNP ID
gwas_p=results.gwas_p     # column of P value
gwas_or=results.gwas_or    # column of odds ratio
gwas_a1=results.gwas_a1    # column of a1 in the GWAS
gwas_maf=results.gwas_maf  # column index of maf in the GWAS

# defin column number for contents in genfile
if filetype.lower()=="vcf":
    geno_id= 2 # column number with rsID
    geno_start=9 # column number of the 1st genotype, in the raw vcf files, after separated by the delimiter of choice
    geno_a1 = 3 # column number that contains the reference allele
    GENO_delim= "\t"
elif filetype.lower()=="gen":
    geno_id = 1
    geno_start=5
    geno_a1=3
    GENO_delim= " "
# List of thresholds:
thresholds=results.thresholds

# file delimiters:
GWAS_delim=results.GWAS_delim


# file names:
#home="/Volumes/mavan/Genotyping_161114/MAVAN_imputed_161121/KIDS_info03/"  #define homefolder path

# Name of GWAS file
gwasFiles=results.GWAS
GWAS_has_header=results.GWAS_header

# programme parameter
log_or=results.log_or  # sepcify whether you want to log your odds ratios
check_ref=results.check_ref # if you know that there are mismatch between the top strand in the genotypes and that of the GWAS, set True. Not checking the reference allele will improve the speed
use_maf=results.use_maf   # wheather to use MAF to check reference allele

# sample file path and name
sampleFilePath=results.sample_file # include the full/relative path and name of the sample file
sampleFileDelim=results.sample_delim  # sample File Delimiter
sampleFileID=results.sample_file_ID   # which column in the sample file has the ID
sample_skip=results.sample_skip  # how many lines to skip so that the sample names can be matched to the genotypes 1-to-1, taking into account the header of the sample file
##output file information

outputPath=results.Output
# Parsing Command-line arguments

genoFileNamePattern=results.GENO
genoFileNames=glob.glob(genoFileNamePattern)



##  start spark context
APP_NAME=results.app_name
spark=SparkSession.builder.appName(APP_NAME).getOrCreate()

# if using spark < 2.0.0, use the pyspark module to make Spark context
# conf = pyspark.SparkConf().setAppName(APP_NAME).set()#.set("spark.serializer", "org.apache.spark.serializer.KryoSerializer")

sc   = spark.sparkContext

#sc = spark.sparkContext
sc.setLogLevel("WARN")
log4jLogger = sc._jvm.org.apache.log4j
LOGGER = log4jLogger.LogManager.getLogger(__name__)
LOGGER.info("Start Reading Files")
LOGGER.info("Using these genoytpe files: ")

for filename in genoFileNames[:min(24, len(genoFileNames))]:
    LOGGER.warn(filename)
if len(genoFileNames)>23:
    LOGGER.info("and more...")

LOGGER.info("total of {} files".format(str(len(genoFileNames))))
# 1. Load files
genodata=sc.textFile(genoFileNamePattern)
LOGGER.info("Using the GWAS file: {}".format(ntpath.basename(gwasFiles)))
gwastable=spark.read.option("header",GWAS_has_header).option("delimiter",GWAS_delim).csv(gwasFiles).cache()
print("Showing top 5 rows of GWAS file")
gwastable.show(5)

# 1.1 Filter GWAS and prepare odds ratio

maxThreshold=max(thresholds)
gwasOddsMapMax=PRS_VCF_utils.filterGWASByP_DF(GWASdf=gwastable, pcolumn=gwas_p, idcolumn=gwas_id, oddscolumn=gwas_or, pHigh=maxThreshold, logOdds=log_or)
gwasOddsMapMaxCA=sc.broadcast(gwasOddsMapMax).value  # Broadcast the map


# ### 2. Initial processing

# at this step, the genotypes are already filtered to keep only the ones in 'gwasOddsMapMax'
bpMap={"A":"T", "T":"A", "C":"G", "G":"C"}
if filetype.lower()=="vcf":
    genointermediate=genodata.filter(lambda line: ("#" not in line)).map(lambda line: line.split(GENO_delim)).filter(lambda line: line[geno_id] in gwasOddsMapMaxCA).map(lambda line: line[0:5]+[chunk.split(":")[3] for chunk in line[geno_start::]]).map(lambda line: line[0:5]+[triplet.split(",") for triplet in line[5::]])

    genotable=genointermediate.map(lambda line: (line[geno_id], list(itertools.chain.from_iterable(line[5::])))).mapValues(lambda geno: [float(x) for x in geno])
    if check_ref:
        if use_maf:
            genoA1f=genointermediate.map(lambda line: (line[geno_id], (line[geno_a1], line[geno_a1+1]), [float(x) for x in list(itertools.chain.from_iterable(line[5::]))])).map(lambda line: (line[0], line[1][0], line[1][1], PRS_VCF_utils.getMaf(line[2]))).toDF(["Snpid_geno", "GenoA1", "GenoA2", "GenoA1f"])
            gwasA1f=gwastable.rdd.map(lambda line:(line[gwas_id], line[gwas_a1], line[gwas_a1+1], line[gwas_maf])).toDF(["Snpid_gwas", "GwasA1", "GwasA2", "GwasMaf"])
            checktable=genoA1f.join(gwasA1f, genoA1f["Snpid_geno"]==gwasA1f["Snpid_gwas"], "inner").cache()

            flagMap=checktable.rdd.map(lambda line: PRS_VCF_utils.checkAlignmentDF(line, bpMap)).collectAsMap()

        else:
            genoA1f=genointermediate.map(lambda line: (line[geno_id], (line[geno_a1], line[geno_a1+1]), [float(x) for x in list(itertools.chain.from_iterable(line[5::]))])).map(lambda line: (line[0], line[1][0], line[1][1])).toDF(["Snpid_geno", "GenoA1", "GenoA2"])

            gwasA1f=gwastable.rdd.map(lambda line:(line[gwas_id], line[gwas_a1], line[gwas_a1+1], line[gwas_maf])).toDF(["Snpid_gwas", "GwasA1", "GwasA2", "GwasMaf"])
            checktable=genoA1f.join(gwasA1f, genoA1f["Snpid_geno"]==gwasA1f["Snpid_gwas"], "inner").cache()

            flagMap=checktable.rdd.map(lambda line: PRS_VCF_utils.checkAlignmentDFnoMAF(line, bpMap)).collectAsMap()

        LOGGER.info("Generate genotype dosage while taking into account difference in strand alignment")
        genotypeMax=genotable.map(lambda line: PRS_VCF_utils.makeGenotypeCheckRef(line, checkMap=flagMap)).cache()

    else:
        LOGGER.info("Generate genotype dosage without checking strand alignments")
        genotypeMax=genotable.map(lambda line: PRS_VCF_utils.makeGenotype(line, gwasOddsMapCA)).cache()

elif filetype.lower()=="gen":
    genotable=genodata.map(lambda line: line.split(GENO_delim)).filter(lambda line: line[geno_id] in gwasOddsMapMaxCA).map(lambda line: (line[geno_id], line[geno_start::])).mapValues(lambda geno: [float(call) for call in geno])

    if check_ref:
        if use_maf:
            genoA1f=genodata.map(lambda line: line.split(GENO_delim)).map(lambda line: (line[geno_id], line[geno_a1], line[geno_a1+1], PRS_VCF_utils.getMaf(line[geno_start::]))).toDF(["Snpid_geno", "GenoA1", "GenoA2"])
            gwasA1f=gwastable.rdd.map(lambda line:(line[gwas_id], line[gwas_a1], line[gwas_a1+1], line[gwas_maf])).toDF(["Snpid_gwas", "GwasA1", "GwasA2", "GwasMaf"])
            checktable=genoA1f.join(gwasA1f, genoA1f["Snpid_geno"]==gwasA1f["Snpid_gwas"], "inner").cache()
            flagMap=checktable.rdd.map(lambda line: PRS_VCF_utils.checkAlignmentDF(line, bpMap)).collectAsMap()
        else:
            genoA1f=genodata.map(lambda line: line.split(GENO_delim)).map(lambda line: (line[geno_id], line[geno_a1], line[geno_a1+1])).toDF(["Snpid_geno", "GenoA1", "GenoA2"])
            gwasA1f=gwastable.rdd.map(lambda line:(line[gwas_id], line[gwas_a1], line[gwas_a1+1])).toDF(["Snpid_gwas", "GwasA1", "GwasA2"])
            checktable=genoA1f.join(gwasA1f, genoA1f["Snpid_geno"]==gwasA1f["Snpid_gwas"], "inner").cache()
            flagMap=checktable.rdd.map(lambda line: PRS_VCF_utils.checkAlignmentDFnoMAF(line, bpMap)).collectAsMap()
        LOGGER.info("Generate genotype dosage while taking into account difference in strand alignment")
        genotypeMax=genotable.map(lambda line: PRS_VCF_utils.makeGenotypeCheckRef(line, checkMap=flagMap)).cache()

    else:
        LOGGER.info("Generate genotype dosage without checking strand alignments")
        genotypeMax=genotable.map(lambda line: PRS_VCF_utils.makeGenotype(line, gwasOddsMapCA)).cache()



samplesize=int(len(genotable.first()[1])/3)
LOGGER.info("Detected {} samples" .format(str(samplesize)))




#genoa1f.map(lambda line:"\t".join([line[0], "\t".join(line[1]), str(line[2])])).saveAsTextFile("../MOMS_info03_maf")
# Calculate PRS at the sepcified thresholds

def calcPRSFromGeno(genotypeRDD, oddsMap):
    totalcount=genotypeRDD.count()
    multiplied=genotypeRDD.map(lambda line:[call * oddsMap[line[0]] for call in line[1]])
    PRS=multiplied.reduce(lambda a,b: map(add, a, b))
    normalizedPRS=[x/totalcount for x in PRS]
    return (totalcount,PRS)



def calcAll(genotypeRDD, gwasRDD, thresholdlist):
    prsMap={}
    if len(thresholdlist)>1:
        thresholdNoMaxSorted=sorted(thresholdlist, reverse=True)
    else:
        thresholdNoMaxSorted=thresholdlist
    start=time()

    for threshold in thresholdNoMaxSorted:
        tic=time()
        gwasFilteredBC=sc.broadcast(PRS_VCF_utils.filterGWASByP_DF(GWASdf=gwasRDD, pcolumn=gwas_p, idcolumn=gwas_id, oddscolumn=gwas_or, pHigh=threshold, logOdds=log_or))
        #gwasFiltered=spark.sql("SELECT snpid, gwas_or_float FROM gwastable WHERE gwas_p_float < {:f}".format(threshold)
        LOGGER.info("Filtered GWAS at threshold of {}. Time spent : {:3.1f} seconds".format( str(threshold), time()-tic) )
        checkpoint=time()
        filteredgenotype=genotypeRDD.filter(lambda line: line[0] in gwasFilteredBC.value)
        if not filteredgenotype.isEmpty():
            prsOther=calcPRSFromGeno(filteredgenotype, gwasFilteredBC.value)
            prsMap[threshold]=prsOther
            LOGGER.info("Finished calculating PRS at threshold of {}. Time spent : {:3.1f} seconds".format(str(threshold), time()-checkpoint))
    return prsMap

prsDict=calcAll(genotypeMax,gwastable, thresholds)
if sampleFilePath!="NOSAMPLE":
    subjNames=PRS_VCF_utils.getSampleNames(sampleFilePath,sampleFileDelim,sampleFileID, skip=1)
    output=PRS_VCF_utils.writePRS(prsDict,  outputPath, samplenames=subjNames)
else:
    LOGGER.info("No sample file input, generating labels for samples.")
    output=PRS_VCF_utils.writePRS(prsDict,  outputPath, samplenames=None)

sc.stop()
