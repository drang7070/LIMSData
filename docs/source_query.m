let
    源 = Sql.Database("10.39.1.122", "LIMS_DATA", [Query="select FINAL, PLANTID, progname, ANALYTE, sampdate, SAMPLE_TYPE ,UNITS from finalresult_all where #(lf)#(lf)PLANTID IN ('沥青焦','针状焦二期','物流装卸') #(lf)#(lf)and progname NOT LIKE '%DMNO%' and progname NOT LIKE '%苯%' and progname NOT LIKE '%萘%' and progname NOT LIKE '%重质%' and progname NOT LIKE '%酚%' and progname NOT LIKE '%古马隆%' and progname NOT LIKE '%泥炮%' and progname NOT LIKE '%T-3485%' and progname NOT LIKE '%T-2104%' and progname NOT LIKE '%T-6001%' #(lf)#(lf)and datediff(d,DATEENTER,getdate())<=140"]),
    移除检测过程 = Table.SelectRows(源, each ([ANALYTE] <> "100℃粘度" and [ANALYTE] <> "试样重（m）" and [ANALYTE] <> "盐酸标准溶液浓度C" and [ANALYTE] <> "V1" and [ANALYTE] <> "V2")),
    替换的值4 = Table.ReplaceValue(移除检测过程,"喹啉不溶物W(%)","喹啉不溶物",Replacer.ReplaceText,{"ANALYTE"}),
    替换的值2 = Table.ReplaceValue(替换的值4,"痕迹","0.02",Replacer.ReplaceText,{"FINAL"}),
    替换的值3 = Table.ReplaceValue(替换的值2,"少量","0.08",Replacer.ReplaceText,{"FINAL"}),
    替换的值1 = Table.ReplaceValue(替换的值3,".0.","0.",Replacer.ReplaceText,{"FINAL"}),
    复制的列 = Table.DuplicateColumn(替换的值1, "FINAL", "复制FINAL"),
    匹配替换 = Table.SplitColumn(复制的列, "复制FINAL",each List.ReplaceMatchingItems(Text.Split(_,","),{{"无效","99999"},{"无","0"},{"未检出","0"},{"不成线","99999"},{"2.14.","2.14"}})),
    替换的值 = Table.ReplaceValue(匹配替换,"NO.","",Replacer.ReplaceText,{"复制FINAL.1"}),
    替换的值5 = Table.ReplaceValue(替换的值,"0..39","0.39",Replacer.ReplaceText,{"复制FINAL.1"}),
    筛选的不含量 = Table.SelectRows(替换的值5, each not Text.Contains([FINAL], "量")),
    更改的类型 = Table.TransformColumnTypes(筛选的不含量,{{"复制FINAL.1", type number}}),
    排序的行 = Table.Sort(更改的类型,{{"PLANTID", Order.Ascending}, {"progname", Order.Ascending}, {"ANALYTE", Order.Ascending}, {"sampdate", Order.Descending}})
in
    排序的行