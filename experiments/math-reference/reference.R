# Independent base-R references; no network or optional packages.
# NIST Yates eddy-current example, R stats paired sleep example, exact binomial.
options(digits=17)
out <- commandArgs(trailingOnly=TRUE)[1]
rows <- data.frame(check=character(), expected=double())
add <- function(key,value) rows <<- rbind(rows,data.frame(check=key,expected=unname(value)))
d <- expand.grid(x1=c(-1,1),x2=c(-1,1),x3=c(-1,1))
d$y <- c(1.70,4.57,.55,3.39,1.51,4.59,.67,4.29)
fit <- lm(y ~ (x1+x2+x3)^2, data=d)
for (key in names(coef(fit))) {
  add(paste0('nist_coef:',key),coef(fit)[key])
  add(paste0('nist_se:',key),coef(summary(fit))[key,'Std. Error'])
  add(paste0('nist_p_value:',key),coef(summary(fit))[key,'Pr(>|t|)'])
  add(paste0('nist_lower:',key),confint(fit)[key,1])
  add(paste0('nist_upper:',key),confint(fit)[key,2])
}
s <- reshape(sleep,direction='wide',idvar='ID',timevar='group')
t <- t.test(s$extra.2,s$extra.1,paired=TRUE)
add('sleep:mean',t$estimate);add('sleep:lower',t$conf.int[1]);add('sleep:upper',t$conf.int[2])
for (df in c(1,2,5,9,30,100)) for (p in c(.025,.5,.95,.975,.995)) add(paste('qt',df,p,sep=':'),qt(p,df))
for (b in c(0,1,3,10)) for (c in c(0,2,7,12)) if (b+c>0) add(paste('exact',b,c,sep=':'),binom.test(b,b+c,p=.5)$p.value)
# Monotone, linear desirability equations, s=1, from Kuhn's CRAN vignette.
for (x in c(75,80,81.09,90,97,100)) add(paste0('dmax:',x),max(0,min(1,(x-80)/17)))
add('desirability:weighted',(.75^2*.4)^(1/3))
write.csv(rows,file=out,row.names=FALSE)
writeLines(c(R.version.string,paste('stats',packageVersion('stats'))),paste0(out,'.version.txt'))
