# --- START OF FILE scheduler.py (VERSÃO CORRIGIDA E COMPLETA) ---

import os
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, RetryAfter

import db_supabase as db

# --- CONSTANTES DE PRODUTO ---
TRIAL_PRODUCT_ID = int(os.getenv("TRIAL_PRODUCT_ID", 3))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))
PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger("Scheduler")
load_dotenv()

# Carrega as mesmas variáveis de ambiente
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TIMEZONE_BR = timezone(timedelta(hours=-3))

# --- FUNÇÃO REUTILIZÁVEL ---
async def kick_user_from_all_groups(user_id: int, bot: Bot):
    """Expulsa e desbane um usuário de todos os grupos listados no DB."""
    supabase_client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    groups_response = await asyncio.to_thread(
        lambda: supabase_client.table('groups').select('telegram_chat_id').execute()
    )
    group_ids = [g['telegram_chat_id'] for g in groups_response.data]

    if not group_ids:
        logger.error(f"CRÍTICO: [kick_user] Nenhum grupo encontrado no DB. Não é possível remover {user_id}.")
        return 0

    removed_count = 0
    for group_id in group_ids:
        try:
            await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
            await bot.unban_chat_member(chat_id=group_id, user_id=user_id, only_if_banned=True)
            logger.info(f"[kick_user] Usuário {user_id} removido do grupo {group_id}.")
            removed_count += 1
        except Forbidden:
            logger.warning(f"[kick_user] Sem permissão para remover {user_id} do grupo {group_id}.")
        except BadRequest as e:
            # --- LÓGICA APRIMORADA AQUI ---
            error_text = str(e).lower()
            if "user not found" in error_text or "member not found" in error_text:
                logger.info(f"[kick_user] Usuário {user_id} já não estava no grupo {group_id}.")
            elif "can't remove chat owner" in error_text:
                logger.warning(f"[kick_user] Não é possível remover o usuário {user_id} do grupo {group_id} porque ele é o proprietário.")
            else:
                logger.error(f"[kick_user] Erro do Telegram ao remover {user_id} do {group_id}: {e}")
            # --- FIM DA LÓGICA APRIMORADA ---

    return removed_count

# --- FUNÇÕES DO SCHEDULER ---

async def find_and_process_expiring_subscriptions(supabase: Client, bot: Bot):
    """Encontra assinaturas que estão para vencer e envia UM ÚNICO aviso."""
    try:
        three_days_from_now = (datetime.now(TIMEZONE_BR) + timedelta(days=3)).isoformat()

        # --- LÓGICA DA CONSULTA CORRIGIDA ---
        # 1. Busca assinaturas que vencem nos próximos 3 dias
        # 2. NOVO FILTRO: Busca APENAS aquelas que AINDA NÃO receberam o aviso
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('id, user:users(telegram_user_id), end_date') # Seleciona apenas o necessário
            .eq('status', 'active')
            .eq('expiry_warning_sent', False) # <-- A MUDANÇA MAIS IMPORTANTE
            .lte('end_date', three_days_from_now)
            .execute()
        )

        if not response.data:
            logger.info("Nenhuma nova assinatura encontrada para enviar aviso de vencimento.")
            return

        logger.info(f"Encontradas {len(response.data)} assinaturas para enviar aviso de vencimento.")

        for sub in response.data:
            user_id = sub.get('user', {}).get('telegram_user_id')
            sub_id = sub.get('id') # Pega o ID da assinatura

            if not user_id or not sub_id:
                continue

            end_date_br = datetime.fromisoformat(sub['end_date']).astimezone(TIMEZONE_BR).strftime('%d/%m/%Y')
            message = f"Olá! 👋 Sua assinatura está próxima de vencer (em {end_date_br}). Para não perder o acesso, use o comando /renovar e efetue o pagamento."

            try:
                await bot.send_message(chat_id=user_id, text=message)
                logger.info(f"Aviso de vencimento enviado para o usuário {user_id} (assinatura {sub_id}).")

                # --- PASSO CRUCIAL: MARCA O AVISO COMO ENVIADO ---
                # Após o envio bem-sucedido, atualiza o status no banco para não enviar de novo.
                await asyncio.to_thread(
                    lambda: supabase.table('subscriptions')
                    .update({'expiry_warning_sent': True})
                    .eq('id', sub_id)
                    .execute()
                )
                logger.info(f"Assinatura {sub_id} marcada como 'aviso enviado'.")
                # --- FIM DO PASSO CRUCIAL ---

                await asyncio.sleep(0.1)

            except RetryAfter as e:
                logger.warning(f"Rate limit atingido ao enviar aviso para {user_id}. Aguardando {e.retry_after} segundos.")
                await asyncio.sleep(e.retry_after)
                # Tenta novamente após a espera
                try:
                    await bot.send_message(chat_id=user_id, text=message)
                    await asyncio.to_thread(
                        lambda: supabase.table('subscriptions')
                        .update({'expiry_warning_sent': True})
                        .eq('id', sub_id)
                        .execute()
                    )
                    logger.info(f"Aviso enviado e assinatura {sub_id} marcada após retry.")
                except Exception as e_inner:
                    logger.error(f"Falha ao reenviar aviso para {user_id} após retry: {e_inner}")
            except (Forbidden, BadRequest):
                logger.warning(f"Não foi possível enviar aviso para o usuário {user_id} (bloqueou o bot?). Marcando como enviado para não tentar de novo.")
                # Mesmo que o bot esteja bloqueado, marcamos como enviado para não continuar tentando
                await asyncio.to_thread(
                    lambda: supabase.table('subscriptions')
                    .update({'expiry_warning_sent': True})
                    .eq('id', sub_id)
                    .execute()
                )
    except Exception as e:
        logger.error(f"Erro ao processar avisos de expiração: {e}", exc_info=True)


async def find_and_process_expired_subscriptions(supabase: Client, bot: Bot):
    """Encontra assinaturas vencidas, remove os usuários e atualiza o status."""
    try:
        now_iso = datetime.now(TIMEZONE_BR).isoformat()

        # Modificamos a consulta para trazer também o product_id
        expired_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('id, product_id, user:users(telegram_user_id)')
            .eq('status', 'active')
            .lt('end_date', now_iso)
            .execute()
        )

        if not expired_response.data:
            logger.info("Nenhuma assinatura vencida encontrada.")
            return

        logger.info(f"Encontradas {len(expired_response.data)} assinaturas vencidas para processar.")

        for sub in expired_response.data:
            user_id = sub.get('user', {}).get('telegram_user_id')
            sub_id = sub.get('id')
            product_id = sub.get('product_id')

            if not user_id:
                continue

            logger.info(f"Processando expiração para o usuário {user_id} (assinatura {sub_id}).")

            # A remoção dos grupos é a mesma para todos
            removed_count = await kick_user_from_all_groups(user_id, bot)

            # Marca a assinatura como 'expired' no banco de dados
            await asyncio.to_thread(
                lambda: supabase.table('subscriptions').update({'status': 'expired'}).eq('id', sub_id).execute()
            )
            logger.info(f"Assinatura {sub_id} do usuário {user_id} marcada como 'expired'. Removido de {removed_count} grupos.")

            # --- LÓGICA CONDICIONAL PARA A MENSAGEM ---
            try:
                if product_id == TRIAL_PRODUCT_ID:
                    # Mensagem personalizada para o fim da degustação
                    product_monthly = await db.get_product_by_id(PRODUCT_ID_MONTHLY)
                    product_lifetime = await db.get_product_by_id(PRODUCT_ID_LIFETIME)

                    text = (
                        "Seu período de degustação de 30 minutos acabou! ✨\n\n"
                        "Gostou do que viu? Garanta seu acesso permanente e não perca nenhuma novidade. "
                        "Escolha um de nossos planos abaixo para continuar na comunidade:"
                    )
                    keyboard = [
                        [InlineKeyboardButton(f"✅ Assinatura Mensal (R$ {product_monthly['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_MONTHLY}')],
                        [InlineKeyboardButton(f"💎 Acesso Vitalício (R$ {product_lifetime['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_LIFETIME}')]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
                else:
                    # Mensagem padrão para assinaturas pagas
                    text = "Sua assinatura expirou e seu acesso aos grupos foi removido. Para voltar, use o comando /renovar."
                    await bot.send_message(chat_id=user_id, text=text)
            except (Forbidden, BadRequest):
                logger.warning(f"Não foi possível notificar o usuário {user_id} sobre a expiração (bloqueou o bot?).")
            except Exception as e:
                logger.error(f"Erro ao enviar mensagem de expiração para {user_id}: {e}")
            # --- FIM DA LÓGICA CONDICIONAL ---

    except Exception as e:
        logger.error(f"Erro CRÍTICO no processo de expiração: {e}", exc_info=True)

