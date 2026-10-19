/**
 * 百度百科「词条直链」表（人工核对）。
 *
 * 为什么需要：部分景点词条**同名多义项**（如「雁门关」有多个义项、
 * 「南禅寺」无锡那座更知名），仅按名称检索可能落到别的义项。
 * 这里记录已核对过的 `/item/<词条名>/<词条ID>` 直链，保证跳到正确义项。
 *
 * 未列出的景点按名称检索（见 KnowledgeView.vue 的 baikeUrl）。
 * 新增时：在百度百科打开正确词条，把地址栏的 `/item/名字/数字` 抄进来即可
 * （`?fromModule=...` 与 `#...` 之类的参数可去掉）。
 */
export const BAIKE_LINKS: Record<string, string> = {
  '青莲寺':
    'https://baike.baidu.com/item/%E9%9D%92%E8%8E%B2%E5%AF%BA/18290',
  '神头岭纪念碑':
    'https://baike.baidu.com/item/%E7%A5%9E%E5%A4%B4%E5%B2%AD%E7%BA%AA%E5%BF%B5%E7%A2%91/554819',
  '和顺县八路军石拐会议纪念园':
    'https://baike.baidu.com/item/%E5%92%8C%E9%A1%BA%E5%8E%BF%E5%85%AB%E8%B7%AF%E5%86%9B%E7%9F%B3%E6%8B%90%E4%BC%9A%E8%AE%AE%E7%BA%AA%E5%BF%B5%E5%9B%AD/69024416',
  '南禅寺':
    'https://baike.baidu.com/item/%E5%8D%97%E7%A6%85%E5%AF%BA/50244',
  '雁门关':
    'https://baike.baidu.com/item/%E9%9B%81%E9%97%A8%E5%85%B3/51788',
  '冠山书院':
    'https://baike.baidu.com/item/%E5%86%A0%E5%B1%B1%E4%B9%A6%E9%99%A2/6715554',
}
