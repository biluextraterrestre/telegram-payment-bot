# --- START OF FILE menu_handlers.py ---
"""
Sistema de Menus Interativos - Navegação 100% por botões
Todas as interações do bot são feitas através de menus com botões inline.
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from telegram.constants import ParseMode

import db_supabase as db
from utils import format_date_br, TIMEZONE_BR

logger = logging.getLogger(__name__)

# === CONSTANTES ===
TRIAL_PRODUCT_ID = int(os.getenv("TRIAL_PRODUCT_ID", 3))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))
PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))

# === EMOJIS PARA MELHOR VISUAL ===
EMOJI = {
    'home': '🏠',
    'status': '📊',
    'buy': '💳',
    'renew': '🔄',
    'support': '💬',
    'info': 'ℹ️',
    'referral': '🎁',
    'back': '◀️',
    'check': '✅',
    'cross': '❌',
    'clock': '⏰',
    'gift': '🎉',
    'link': '🔗',
    'groups': '👥',
    'trial': '🆓'
}

# === MENU PRINCIPAL ===
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    """
    Exibe o menu principal com todas as opções disponíveis.
    edit=True: edita a mensagem existente (para navegação)
    edit=False: envia nova mensagem (para /start)
    """
    user = update.effective_user
    
    # Mensagem de boas-vindas personalizada
    welcome_text = (
        f"Olá, {user.first_name}! 👋\n\n"
        f"Seja bem-vindo(a) ao nosso bot de gerenciamento de assinaturas.\n"
        f"Escolha uma opção abaixo para continuar:"
    )
    
    # Construção do teclado
    keyboard = [
        [
            InlineKeyboardButton(
                f"{EMOJI['status']} Minha Assinatura",
                callback_data='menu_subscription_status'
            )
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI['buy']} Ver Planos",
                callback_data='menu_view_plans'
            ),
            InlineKeyboardButton(
                f"{EMOJI['trial']} Testar Grátis",
                callback_data='menu_trial'
            )
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI['groups']} Meus Grupos",
                callback_data='menu_my_groups'
            )
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI['referral']} Programa de Indicação",
                callback_data='menu_referral'
            )
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI['support']} Solicitar Suporte",
                callback_data='menu_support'
            ),
            InlineKeyboardButton(
                f"{EMOJI['info']} Informações",
                callback_data='menu_info'
            )
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text=welcome_text,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            text=welcome_text,
            reply_markup=reply_markup
        )

# === HANDLER DO COMANDO /start ===
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start - exibe o menu principal"""
    await show_main_menu(update, context, edit=False)

# === MENU: STATUS DA ASSINATURA ===
async def show_subscription_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra o status atual da assinatura do usuário"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    # Busca informações do usuário e assinatura ativa
    user_db = await db.get_user_by_telegram_id(user_id)
    if not user_db:
        await query.edit_message_text(
            text="❌ Usuário não encontrado no sistema. Use /start para começar.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['home']} Menu Principal", callback_data='menu_main')
            ]])
        )
        return
    
    subscription = await db.get_active_subscription(user_db['id'])
    
    if not subscription:
        text = (
            "📭 *Nenhuma assinatura ativa*\n\n"
            "Você ainda não possui uma assinatura ativa.\n"
            "Escolha um dos nossos planos para começar!"
        )
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['buy']} Ver Planos", callback_data='menu_view_plans')],
            [InlineKeyboardButton(f"{EMOJI['trial']} Testar Grátis (30 min)", callback_data='menu_trial')],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]
    else:
        # Busca informações do produto
        product = await db.get_product_by_id(subscription['product_id'])
        
        # Calcula dias restantes
        end_date = datetime.fromisoformat(subscription['end_date'])
        days_left = (end_date - datetime.now(TIMEZONE_BR)).days
        
        # Status visual
        if days_left > 7:
            status_emoji = EMOJI['check']
            status_text = "Ativa"
        elif days_left > 0:
            status_emoji = EMOJI['clock']
            status_text = f"Expira em {days_left} dia(s)"
        else:
            status_emoji = EMOJI['cross']
            status_text = "Expirada"
        
        text = (
            f"📊 *Status da Assinatura*\n\n"
            f"🏷️ *Plano:* {product['name']}\n"
            f"{status_emoji} *Status:* {status_text}\n"
            f"📅 *Início:* {format_date_br(subscription['start_date'])}\n"
            f"📅 *Término:* {format_date_br(subscription['end_date'])}\n"
        )
        
        if subscription.get('original_price') and subscription.get('final_price'):
            if subscription['original_price'] != subscription['final_price']:
                text += f"\n💰 *Valor Pago:* R$ {subscription['final_price']:.2f} (desconto aplicado)"
            else:
                text += f"\n💰 *Valor Pago:* R$ {subscription['final_price']:.2f}"
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['renew']} Renovar/Estender", callback_data='menu_view_plans')],
            [InlineKeyboardButton(f"{EMOJI['link']} Obter Links dos Grupos", callback_data='menu_get_links')],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: VER PLANOS ===
async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe todos os planos disponíveis para compra"""
    query = update.callback_query
    await query.answer()
    
    # Busca todos os produtos ativos
    products = await db.get_all_products()
    
    if not products:
        await query.edit_message_text(
            text="❌ Nenhum plano disponível no momento. Tente novamente mais tarde.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')
            ]])
        )
        return
    
    text = "💳 *Planos Disponíveis*\n\nEscolha o plano ideal para você:\n\n"
    
    keyboard = []
    
    for product in products:
        # Pula o produto de trial
        if product['id'] == TRIAL_PRODUCT_ID:
            continue
            
        # Descrição do produto
        duration_text = ""
        if product.get('duration_days'):
            if product['duration_days'] >= 365:
                duration_text = f" ({product['duration_days'] // 365} ano(s))"
            else:
                duration_text = f" ({product['duration_days']} dias)"
        
        recurrent_text = " 🔄" if product.get('is_recurrent') else ""
        
        text += f"• *{product['name']}*{duration_text}{recurrent_text}\n"
        text += f"  💵 R$ {product['price']:.2f}\n\n"
        
        # Botão para este produto
        keyboard.append([
            InlineKeyboardButton(
                f"✅ Assinar - {product['name']} (R$ {product['price']:.2f})",
                callback_data=f'pay_{product["id"]}'
            )
        ])
    
    # Opção de usar cupom
    keyboard.append([
        InlineKeyboardButton(
            f"🎫 Tenho um Cupom de Desconto",
            callback_data='menu_apply_coupon'
        )
    ])
    
    keyboard.append([
        InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: TRIAL/DEGUSTAÇÃO ===
async def show_trial_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe informações sobre o período de degustação"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user_db = await db.get_user_by_telegram_id(user_id)
    
    if not user_db:
        await query.edit_message_text(
            text="❌ Erro ao buscar suas informações. Use /start para começar.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['home']} Menu Principal", callback_data='menu_main')
            ]])
        )
        return
    
    # Verifica se já usou o trial
    has_trial = await db.user_has_trial_subscription(user_db['id'])
    
    if has_trial:
        text = (
            "⚠️ *Degustação já utilizada*\n\n"
            "Você já utilizou seu período de degustação gratuita.\n"
            "Para continuar aproveitando nosso conteúdo, escolha um dos nossos planos!"
        )
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['buy']} Ver Planos", callback_data='menu_view_plans')],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]
    else:
        text = (
            f"{EMOJI['trial']} *Período de Degustação Gratuito*\n\n"
            f"🎁 Experimente gratuitamente por *30 minutos*!\n\n"
            f"📌 *O que você recebe:*\n"
            f"• Acesso a todos os nossos grupos\n"
            f"• Conteúdo completo sem restrições\n"
            f"• Veja na prática o valor do nosso serviço\n\n"
            f"⏰ O período começa assim que você confirmar.\n"
            f"Após 30 minutos, você poderá escolher um plano para continuar."
        )
        keyboard = [
            [InlineKeyboardButton(
                f"{EMOJI['gift']} Começar Degustação Agora",
                callback_data='confirm_trial'
            )],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: MEUS GRUPOS ===
async def show_my_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra os grupos que o usuário tem acesso"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user_db = await db.get_user_by_telegram_id(user_id)
    
    if not user_db:
        await query.edit_message_text(
            text="❌ Erro ao buscar suas informações.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['home']} Menu Principal", callback_data='menu_main')
            ]])
        )
        return
    
    # Verifica se tem assinatura ativa
    subscription = await db.get_active_subscription(user_db['id'])
    
    if not subscription:
        text = (
            "📭 *Você não possui acesso ativo*\n\n"
            "Para acessar nossos grupos exclusivos, você precisa de uma assinatura ativa.\n"
            "Experimente grátis por 30 minutos ou escolha um plano!"
        )
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['trial']} Testar Grátis", callback_data='menu_trial')],
            [InlineKeyboardButton(f"{EMOJI['buy']} Ver Planos", callback_data='menu_view_plans')],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]
    else:
        # Calcula dias restantes
        end_date = datetime.fromisoformat(subscription['end_date'])
        time_left = end_date - datetime.now(TIMEZONE_BR)
        
        if time_left.days > 0:
            time_text = f"{time_left.days} dia(s)"
        else:
            hours_left = time_left.seconds // 3600
            minutes_left = (time_left.seconds % 3600) // 60
            time_text = f"{hours_left}h {minutes_left}min"
        
        text = (
            f"{EMOJI['groups']} *Seus Grupos de Acesso*\n\n"
            f"✅ Você tem acesso ativo!\n"
            f"⏰ Tempo restante: {time_text}\n\n"
            f"Clique no botão abaixo para receber os links de acesso a todos os grupos.\n\n"
            f"⚠️ *Importante:*\n"
            f"• Os links expiram em 2 horas\n"
            f"• Cada link só pode ser usado uma vez\n"
            f"• Entre em até 3 grupos por vez (aguarde ~30min entre lotes)"
        )
        keyboard = [
            [InlineKeyboardButton(
                f"{EMOJI['link']} Enviar Links de Acesso",
                callback_data='menu_get_links'
            )],
            [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: PROGRAMA DE INDICAÇÃO ===
async def show_referral_program(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra informações do programa de indicação"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user_db = await db.get_user_by_telegram_id(user_id)
    
    if not user_db:
        await query.edit_message_text(
            text="❌ Erro ao buscar suas informações.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{EMOJI['home']} Menu Principal", callback_data='menu_main')
            ]])
        )
        return
    
    referral_code = user_db.get('referral_code', 'N/A')
    
    # Busca estatísticas de indicações
    referrals_count = await db.count_user_referrals(user_db['id'])
    
    text = (
        f"{EMOJI['referral']} *Programa de Indicação*\n\n"
        f"Indique amigos e ganhe benefícios!\n\n"
        f"📋 *Seu código:* `{referral_code}`\n"
        f"👥 *Indicações realizadas:* {referrals_count}\n\n"
        f"🎁 *Como funciona:*\n"
        f"1. Compartilhe seu código com amigos\n"
        f"2. Eles usam o código ao se cadastrar\n"
        f"3. Você ganha recompensas quando eles assinarem\n\n"
        f"💡 *Dica:* Quanto mais indicar, mais benefícios você acumula!"
    )
    
    keyboard = [
        [InlineKeyboardButton(
            "📤 Compartilhar Código",
            url=f"https://t.me/share/url?url=Use o código {referral_code} para se cadastrar!"
        )],
        [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: SUPORTE ===
async def show_support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu de opções de suporte"""
    query = update.callback_query
    await query.answer()
    
    text = (
        f"{EMOJI['support']} *Central de Suporte*\n\n"
        f"Como podemos ajudar você hoje?\n\n"
        f"Escolha uma das opções abaixo:"
    )
    
    keyboard = [
        [InlineKeyboardButton(
            "🔗 Reenviar Links dos Grupos",
            callback_data='support_resend_links'
        )],
        [InlineKeyboardButton(
            "💳 Problema com Pagamento",
            callback_data='support_payment_issue'
        )],
        [InlineKeyboardButton(
            "❓ Outras Dúvidas",
            callback_data='support_other'
        )],
        [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === MENU: INFORMAÇÕES ===
async def show_info_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe informações gerais sobre o serviço"""
    query = update.callback_query
    await query.answer()
    
    text = (
        f"{EMOJI['info']} *Informações do Serviço*\n\n"
        f"📚 *Sobre nós:*\n"
        f"Oferecemos acesso a grupos exclusivos com conteúdo premium de alta qualidade.\n\n"
        f"❓ *Dúvidas Frequentes:*\n\n"
        f"*• Quanto tempo dura minha assinatura?*\n"
        f"  Depende do plano escolhido. Veja em 'Ver Planos'.\n\n"
        f"*• Posso testar antes de comprar?*\n"
        f"  Sim! Oferecemos 30 minutos grátis.\n\n"
        f"*• Como renovo minha assinatura?*\n"
        f"  Acesse 'Minha Assinatura' > 'Renovar'.\n\n"
        f"*• Não recebi os links dos grupos*\n"
        f"  Use 'Solicitar Suporte' > 'Reenviar Links'.\n\n"
        f"💬 *Precisa de mais ajuda?*\n"
        f"Use nossa Central de Suporte!"
    )
    
    keyboard = [
        [InlineKeyboardButton(f"{EMOJI['support']} Central de Suporte", callback_data='menu_support')],
        [InlineKeyboardButton(f"{EMOJI['back']} Voltar", callback_data='menu_main')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === HANDLER PARA APLICAR CUPOM ===
async def show_coupon_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Instrui o usuário a enviar o código do cupom"""
    query = update.callback_query
    await query.answer()
    
    text = (
        "🎫 *Usar Cupom de Desconto*\n\n"
        "Digite o código do seu cupom na próxima mensagem.\n\n"
        "Exemplo: `DESCONTO10`\n\n"
        "Após validar o cupom, você poderá escolher seu plano com o desconto aplicado."
    )
    
    keyboard = [[InlineKeyboardButton(f"{EMOJI['back']} Cancelar", callback_data='menu_view_plans')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Define o estado para aguardar o cupom
    context.user_data['awaiting_coupon'] = True

# === HANDLER DE CALLBACKS ===
async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router principal para todos os callbacks de menu"""
    query = update.callback_query
    data = query.data
    
    # Roteamento baseado no callback_data
    if data == 'menu_main':
        await show_main_menu(update, context, edit=True)
    elif data == 'menu_subscription_status':
        await show_subscription_status(update, context)
    elif data == 'menu_view_plans':
        await show_plans(update, context)
    elif data == 'menu_trial':
        await show_trial_info(update, context)
    elif data == 'menu_my_groups':
        await show_my_groups(update, context)
    elif data == 'menu_referral':
        await show_referral_program(update, context)
    elif data == 'menu_support':
        await show_support_menu(update, context)
    elif data == 'menu_info':
        await show_info_menu(update, context)
    elif data == 'menu_apply_coupon':
        await show_coupon_input(update, context)
    # Adicione outros handlers conforme necessário
    else:
        # Para callbacks não tratados aqui, deixa passar para outros handlers
        return

# === REGISTRAR HANDLERS ===
def register_menu_handlers(application):
    """Registra todos os handlers de menu no bot"""
    # Comando /start
    application.add_handler(CommandHandler("start", start_command))
    
    # Callbacks de menu (padrão menu_*)
    application.add_handler(CallbackQueryHandler(
        handle_menu_callback,
        pattern='^menu_'
    ))
    
    logger.info("✅ Handlers de menu registrados com sucesso!")
