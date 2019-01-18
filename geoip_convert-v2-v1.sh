#!/bin/bash

# the bash part is written by emphazer
# https://github.com/emphazer
# https://github.com/emphazer/GeoIP_convert-v2-v1

# requirements python2.7
# ipaddr  == (2.2.0) 
# pygeoip == (0.3.2)

# chmod +x geoip_convert-v2-v1.sh
# ./geoip_convert-v2-v1.sh NAME

# choose a name for the release
NAME="$1"

AWK=`which gawk      2>>/dev/null` ; AWK="${AWK:-awk}"

if python -V 2>&1 | grep -q "Python 2"; then 
        PYT="python"
    else
        PYT=`which python2.7 2>>/dev/null` ; PYT="${PYT:-python2}"
fi

if ! $PYT -V 2>&1 | grep -q "Python 2"; then 
        echo "python2 is needed"
        exit
fi

if pip -V 2>&1 | grep -q "python 2"; then
        PIP="pip"
    else
        PIP=`which pip2.7    2>>/dev/null` ; PIP="${PIP:-pip2}"
fi

if ! $PIP -V 2>&1 | grep -q "python 2"; then 
        echo "python pip is needed"
        exit
fi

DATE_TODAY=$(date +"%Y%m%d")

mkdir $DATE_TODAY && cd $DATE_TODAY && (

        $PIP install --upgrade pygeoip==0.3.2 ipaddr==2.2.0 &>/dev/null

        curl -s https://raw.githubusercontent.com/emphazer/mmdb-convert/master/mmdb-convert.py         > mmdb-convert.py
        curl -s https://raw.githubusercontent.com/emphazer/mmdb-convert/master/mmdb-convert-country.py > mmdb-convert-country.py
        curl -s https://raw.githubusercontent.com/emphazer/mmutils/master/csv2dat.py                   > csv2dat.py

        # set the name for the release
        sed -i -r '/csv2dat.py [0-9]{8} Build/ s@csv2dat.py [0-9]{8}@'${NAME:-NAME}' '${DATE_TODAY:-20180000}'@' csv2dat.py

        chmod +x mmdb-convert*.py csv2dat.py

        # download the geolite2 country database
        curl -s http://geolite.maxmind.com/download/geoip/database/GeoLite2-Country.tar.gz     > GeoLite2-Country.tar.gz 
        # extract the decimal ip range with country code
        tar -xzf GeoLite2-Country.tar.gz && \
        $PYT ./mmdb-convert.py         GeoLite2-Country_*/GeoLite2-Country.mmdb
        $PYT ./mmdb-convert-country.py GeoLite2-Country_*/GeoLite2-Country.mmdb

        # here comes the magical part
        # convert the decimal ip adresses to ipv4 
        # and print it in the classic csv format
        grep ^'[0-9]' geoip | \
        awk -F',' {'print "\"" rshift(and($1, 0xFF000000), 24) "." rshift(and($1, 0x00FF0000), 16) "." rshift(and($1, 0x0000FF00), 8) "." and($1, 0x000000FF) "\",\"" rshift(and($2, 0xFF000000), 24) "." rshift(and($2, 0x00FF0000), 16) "." rshift(and($2, 0x0000FF00), 8) "." and($2, 0x000000FF) "\",\"" $1 "\",\"" $2 "\",\"" $3 "\",\"\""'} >GeoIPC.csv

        grep ^'[0-9]' geoip_country | \
        awk -F',' {'print "\"" rshift(and($1, 0xFF000000), 24) "." rshift(and($1, 0x00FF0000), 16) "." rshift(and($1, 0x0000FF00), 8) "." and($1, 0x000000FF) "\",\"" rshift(and($2, 0xFF000000), 24) "." rshift(and($2, 0x00FF0000), 16) "." rshift(and($2, 0x0000FF00), 8) "." and($2, 0x000000FF) "\",\"" $1 "\",\"" $2 "\",\"" $3 "\",\"\""'} >GeoIPC_country.csv

        # generate the GeoIP.dat file with the csv file 
        $PYT ./csv2dat.py -w GeoIP.dat         mmcountry GeoIPC.csv
        $PYT ./csv2dat.py -w GeoIP_country.dat mmcountry GeoIPC_country.csv

        # test the result
        geoiplookup 8.8.8.8 -f GeoIP.dat -i
        geoiplookup 8.8.8.8 -f GeoIP.dat -v

        # it should look like this now:
        # GeoIP Country Edition: Custom 20181025 Build
        )

